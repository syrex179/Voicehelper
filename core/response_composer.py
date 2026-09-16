"""Pure, deterministic composition of user-facing assistant responses."""
from dataclasses import dataclass, field
from .response_enricher import ResponseEnricher

@dataclass(frozen=True)
class Response:
    text: str
    response_type: str
    speak: bool=True
    metadata: dict=field(default_factory=dict)

class ResponseComposer:
    """Formats structured outcomes only; it never executes actions or stores data."""
    _ACTION_TEXT={"OPEN_BROWSER":"Готово, браузер открыт.","OPEN_YOUTUBE":"Готово, YouTube открыт.","SET_VOLUME":"Готово, громкость установлена."}
    _PENDING_TEXT={"browser_clarification":"Мне нужен адрес или название сайта.","volume_clarification":"Мне нужно значение громкости от 0 до 100.","folder_clarification":"Мне нужна папка: Загрузки, Документы или Рабочий стол."}
    _INFORMATIONAL={"HELP","SESSION_RECAP","SHOW_ROUTINE","LIST_ROUTINES","PENDING_DIALOGUE_RECAP"}
    def __init__(self): self.enricher=ResponseEnricher()

    def compose(self, *, turn=None, sequence=None, intent="", success=True, text="", response_type="", pending=None, session_state="", metadata=None):
        if turn is not None:
            action=dict(turn.action_result)
            metadata=action.get("metadata",{})
            # A valid provider conversation/information result has one
            # authoritative user-facing text.  Do not run it through generic
            # action enrichment (which may otherwise legitimately say
            # "Готово.").
            if (turn.response_type in {"conversational","informational"}
                    and metadata.get("authoritative_response_text")):
                return self.compose(intent=turn.intent,success=action.get("success",False),text=action.get("message","") ,response_type=turn.response_type,pending=dict(turn.pending_state),session_state=turn.session_state,metadata=metadata)
            enriched=self.enricher.enrich(turn,sequence)
            special=metadata.get("composer_owned_template") in {"open_browser_success","set_volume_success","open_youtube_success"}
            text=enriched.text if (special or enriched.metadata.get("generic_owned")) and enriched.text else action.get("message","") or enriched.text
            return self.compose(intent=turn.intent,success=action.get("success",False),text=text,response_type=turn.response_type,pending=dict(turn.pending_state),session_state=turn.session_state,metadata=metadata)
        metadata=dict(metadata or {})
        kind=response_type or self._type_for(intent,success,pending,metadata)
        if text: return Response(str(text),kind,True,metadata)
        if not success: return Response("Не удалось выполнить запрос.","action_failure",True,metadata)
        if pending:
            return Response(self._PENDING_TEXT.get(pending.get("kind"),"Мне нужен ответ для текущего диалога."),"clarification",True,metadata)
        if intent=="SET_VOLUME" and metadata.get("level") is not None: return Response(f"Готово, громкость установлена на {metadata['level']}%.","action_success",True,metadata)
        if intent in self._ACTION_TEXT: return Response(self._ACTION_TEXT[intent],"action_success",True,metadata)
        if kind=="reference_replay": return Response("Хорошо, повторяю.",kind,True,metadata)
        if kind=="correction": return Response("Хорошо, исправляю.",kind,True,metadata)
        if kind=="social": return Response("Хорошо.",kind,True,metadata)
        if kind=="status": return Response("Да, я готов.",kind,True,metadata)
        if kind=="help": return Response("Скажи, чем я могу помочь.",kind,True,metadata)
        if kind=="informational": return Response("Вот что я знаю.",kind,True,metadata)
        if kind=="unknown": return Response("Не совсем понял. Повтори, пожалуйста.",kind,True,metadata)
        return Response("Готово.",kind,True,metadata)

    def response_type_for(self, intent="", success=True, pending=None, metadata=None):
        return self._type_for(intent,success,pending,dict(metadata or {}))

    def _type_for(self,intent,success,pending,metadata):
        if not success: return "action_failure"
        if pending: return "clarification"
        if metadata.get("corrected"): return "correction"
        if metadata.get("reference_replay"): return "reference_replay"
        if intent in self._INFORMATIONAL: return "informational" if intent!="HELP" else "help"
        if intent.startswith("SESSION_GREETING") or intent.startswith("SESSION_THANKS") or intent.startswith("SESSION_GOODBYE") or intent=="SESSION_ACKNOWLEDGEMENT": return "social"
        if intent=="SESSION_STATUS_QUERY": return "status"
        if intent=="UNKNOWN": return "unknown"
        return "action_success"
