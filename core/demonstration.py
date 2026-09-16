"""Safe recorder: observes only structured, allow-listed router commands."""
from urllib.parse import urlparse
from .models import Result

class DemonstrationRecorder:
    ALLOWED={"OPEN_APPLICATION","OPEN_FOLDER","OPEN_BROWSER","OPEN_YOUTUBE","SEARCH_YOUTUBE","SEARCH_WEB","SET_VOLUME","VOLUME_UP","VOLUME_DOWN","MUTE","UNMUTE","PLAY_PAUSE","NEXT_TRACK","PREVIOUS_TRACK","TAKE_SCREENSHOT","SHOW_DESKTOP"}
    # These are routine schemas, not parser schemas.  The recorder receives a
    # successful Router event, then keeps only fields which are part of the
    # action's validated, replayable contract.  In particular, ``query`` is a
    # parser scratch field for OPEN_BROWSER/OPEN_YOUTUBE, but it is canonical
    # for searches and folders.
    _FIELDS={
        "OPEN_APPLICATION":{"application"}, "OPEN_FOLDER":{"query"},
        "OPEN_BROWSER":{"url"}, "OPEN_YOUTUBE":set(),
        "SEARCH_YOUTUBE":{"query"}, "SEARCH_WEB":{"query"},
        "SET_VOLUME":{"level"}, "VOLUME_UP":set(), "VOLUME_DOWN":set(),
        "MUTE":set(), "UNMUTE":set(), "PLAY_PAUSE":set(), "NEXT_TRACK":set(),
        "PREVIOUS_TRACK":set(), "TAKE_SCREENSHOT":set(), "SHOW_DESKTOP":set(),
    }
    def __init__(self, events):
        self.events=events; self.recording=False; self.actions=[]; events.subscribe("command_routed",self._record)
    def start(self): self.recording=True; self.actions=[]; return Result(True,"Режим обучения включён. Выполняйте разрешённые команды JARVIS.")
    def cancel(self): self.recording=False; self.actions=[]; return Result(True,"Обучение отменено.")
    def stop(self):
        self.recording=False
        if not self.actions: return Result(False,"Разрешённых действий не записано.")
        return Result(True,"Обучение завершено. Как назвать новый режим?",{"actions":list(self.actions)})
    def _record(self, command, result):
        if self.recording and result.ok and command.intent in self.ALLOWED:
            parameters=self._canonical_parameters(command.intent,command.parameters)
            if parameters is not None:
                self.actions.append({"intent":command.intent,"parameters":parameters})
    @classmethod
    def _canonical_parameters(cls, intent, parameters):
        source=dict(parameters or {})
        fields=cls._FIELDS.get(intent)
        if fields is None: return None
        values={key:source[key] for key in fields if key in source}
        if intent=="SET_VOLUME":
            level=values.get("level")
            return {"level":level} if isinstance(level,int) and not isinstance(level,bool) and 0<=level<=100 else None
        if intent=="OPEN_BROWSER" and "url" in values:
            parsed=urlparse(values["url"]) if isinstance(values["url"],str) else None
            if not parsed or parsed.scheme not in {"http","https"} or not parsed.netloc: return None
        if intent in {"OPEN_APPLICATION","OPEN_FOLDER","SEARCH_YOUTUBE","SEARCH_WEB"}:
            field=next(iter(fields)); value=values.get(field)
            if not isinstance(value,str) or not value.strip(): return None
            values[field]=value.strip()
        return values
