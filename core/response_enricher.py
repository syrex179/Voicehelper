"""Pure deterministic enrichment from safe ConversationTurn facts only."""
from dataclasses import dataclass, field
import re
from .structured_result_inventory import StructuredResultInventory

@dataclass(frozen=True)
class ResponseEnrichment:
    text: str=""
    metadata: dict=field(default_factory=dict)

class ResponseEnricher:
    _PENDING={"browser_clarification":"Я жду название или адрес сайта.","volume_clarification":"Я жду значение громкости от 0 до 100.","folder_clarification":"Я жду: Загрузки, Документы или Рабочий стол."}
    _TARGETS={"OPEN_YOUTUBE":"YouTube"}
    @staticmethod
    def _safe_target(value): return value if isinstance(value,str) and 1<=len(value)<=64 and re.fullmatch(r"[\w\s.-]+",value,re.U) else ""
    def __init__(self): self.inventory=StructuredResultInventory()
    def enrich(self, turn, sequence=None):
        action=turn.action_result; metadata=action.get("metadata",{})
        special=metadata.get("composer_owned_template")
        if action.get("action_type")=="OPEN_BROWSER" and special=="open_browser_success": return ResponseEnrichment("Готово, браузер открыт.",{"safe":True,"composer_owned":True})
        if action.get("action_type")=="OPEN_YOUTUBE" and special=="open_youtube_success": return ResponseEnrichment("Готово, YouTube открыт.",{"safe":True,"composer_owned":True})
        category_info=self.inventory.classify(turn); category=category_info.category
        if category=="generic_failure" and not category_info.authoritative_message_safe:
            return ResponseEnrichment("Не удалось выполнить запрос.",{"safe":True,"generic_owned":True})
        if turn.response_type=="reference_replay":
            target=turn.reference_context.get("last_safe_action_target","")
            return ResponseEnrichment(f"Повторяю открытие {target}." if target else "Хорошо, повторяю.",{"safe":True})
        if turn.response_type=="correction":
            level=metadata.get("level")
            return ResponseEnrichment(f"Хорошо, исправляю значение на {level}." if isinstance(level,int) and 0<=level<=100 else "Хорошо, исправляю.",{"safe":True})
        if turn.pending_state:
            return ResponseEnrichment(self._PENDING.get(turn.pending_state.get("kind"),""),{"safe":True})
        if category=="safe_target":
            target=metadata.get("safe_target") or self._TARGETS.get(action.get("action_type"),"")
            if target:
                if action.get("action_type") in {"OPEN_BROWSER","OPEN_YOUTUBE"}:
                    return ResponseEnrichment(f"Готово, браузер открыт: {target}.",{"safe":True,"generic_owned":True})
                return ResponseEnrichment(f"Готово, открываю {target}.",{"safe":True,"generic_owned":True})
        if category=="validated_numeric":
            value=metadata.get("level",metadata.get("value"))
            if action.get("action_type")=="SET_VOLUME":
                return ResponseEnrichment(f"Готово, громкость установлена на {value}%.",{"safe":True,"generic_owned":True})
            return ResponseEnrichment(f"Готово, значение установлено на {value}.",{"safe":True,"generic_owned":True})
        if category=="generic_success": return ResponseEnrichment("Готово.",{"safe":True,"generic_owned":True})
        if category=="legacy_only" and not self.inventory.is_safe_authoritative_message(action.get("message","")):
            return ResponseEnrichment("Готово.",{"safe":True,"generic_owned":True,"legacy_fallback":True})
        return ResponseEnrichment()
