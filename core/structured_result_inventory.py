"""Read-only category mapping for safe generic response eligibility."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class ResultCategory:
    category: str
    reason: str
    fallback: str=""
    authoritative_message_safe: bool=False

class StructuredResultInventory:
    """Classifies existing structured outcomes; it never executes or mutates them."""
    _KNOWN_TARGETS={"OPEN_YOUTUBE":"YouTube","OPEN_FOLDER":"Документы","OPEN_FOLDER_DOWNLOADS":"Загрузки","OPEN_FOLDER_DESKTOP":"Рабочий стол"}
    _LEGACY_ACTIONS={"SEARCH_WEB","SEARCH_YOUTUBE","OPEN_FOLDER","OPEN_APPLICATION","SEARCH_FILE","SHOW_FOUND_FILE","SHOW_FOUND_FOLDER","TAKE_SCREENSHOT","OPEN_SETTINGS","OPEN_TASK_MANAGER","SHOW_DESKTOP","LOCK","SHUTDOWN","RESTART","PLAY_PAUSE","NEXT_TRACK","PREVIOUS_TRACK","VOLUME_UP","VOLUME_DOWN","MUTE","UNMUTE"}
    _SAFE_FAILURE_MESSAGES={"Разрешены только URL http:// или https://.","Не найден установленный браузер.","Уточните папку: Загрузки, Документы или Рабочий стол.","Выберите результат поиска.","Файлы не найдены.","Процедура не найдена.","Я не понял команду."}
    _SAFE_AUTHORITATIVE_MESSAGES={"ok","done","success","completed"}
    _RAW_FAILURE=re.compile(r"(?:traceback|exception|error|errno|runtimeerror|oserror|stack|token|secret|password|api[_-]?key|authorization|credential|cookie|\b(?:cmd|powershell|subprocess)\b|[a-z]:\\|(?:^|\s)/(?:[\w.-]+/)+|\\\\|\[.*\]|<[^>]+>)",re.I)

    def is_safe_authoritative_failure(self,message):
        """Only user-safe Russian prose may bypass the generic failure fallback."""
        text=message.strip() if isinstance(message,str) else ""
        if text in self._SAFE_FAILURE_MESSAGES: return True
        return bool(1<=len(text)<=240 and re.search(r"[А-Яа-яЁё]",text) and not self._RAW_FAILURE.search(text))

    def is_safe_authoritative_message(self,message):
        """Reject raw paths, URLs, commands, and technical details from legacy replies."""
        text=message.strip() if isinstance(message,str) else ""
        if text.casefold() in self._SAFE_AUTHORITATIVE_MESSAGES: return True
        return bool(1<=len(text)<=240 and re.search(r"[А-Яа-яЁё]",text) and not self._RAW_FAILURE.search(text) and not re.search(r"(?:file:|about:|https?://)",text,re.I))

    def classify(self, turn):
        action=turn.action_result; metadata=action.get("metadata",{})
        if not action.get("success"):
            safe=self.is_safe_authoritative_failure(action.get("message",""))
            return ResultCategory("generic_failure","safe authoritative failure" if safe else "technical or missing failure message","authoritative Result.message" if safe else "generic safe failure response",safe)
        if metadata.get("safe_target") in set(self._KNOWN_TARGETS.values()): return ResultCategory("safe_target","normalized allow-listed target")
        if isinstance(metadata.get("level"),int) and 0<=metadata["level"]<=100: return ResultCategory("validated_numeric","validated volume level")
        if metadata.get("validated_value") is True and isinstance(metadata.get("value"),(int,float)) and not isinstance(metadata.get("value"),bool): return ResultCategory("validated_numeric","explicitly validated primitive value")
        if not action.get("message") and action.get("action_type"): return ResultCategory("generic_success","structured success without message")
        if action.get("action_type") in self._LEGACY_ACTIONS: return ResultCategory("legacy_only","metadata may contain raw paths, URLs, or unnormalized targets","authoritative Result.message")
        return ResultCategory("legacy_only","insufficient safe structured metadata","authoritative Result.message")
