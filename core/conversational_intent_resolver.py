"""Bounded deterministic fallback from conversational phrasing to existing intents."""
from dataclasses import dataclass, field
import re
from .models import Command

@dataclass(frozen=True)
class ResolvedIntent:
    intent: str="UNKNOWN"
    confidence: str="none"
    source: str="resolver"
    parameters: dict=field(default_factory=dict)
    requires_clarification: bool=False
    reference_to_session: bool=False
    no_execute: bool=False
    def command(self,text): return Command(self.intent,dict(self.parameters),text)

class ConversationalIntentResolver:
    """Pure phrase groups; it reads snapshots only and never executes or mutates."""
    def resolve(self,text,*,exact=None,pending=None,session_context=None):
        if exact is not None and getattr(exact,"intent","")!="UNKNOWN":
            return ResolvedIntent(exact.intent,"exact","parser",dict(getattr(exact,"parameters",{}) or {}))
        phrase=" ".join(str(text or "").casefold().split()).strip("?.!")
        if not phrase: return ResolvedIntent()
        if phrase in {"я передумал","не надо","давай отменим","отмена"}:
            return ResolvedIntent("CANCEL_PENDING_DIALOGUE","high",requires_clarification=not bool(pending),no_execute=not bool(pending))
        if phrase in {"повтори ответ","скажи ещё раз","скажи еще раз","я не понял повтори"}:
            return ResolvedIntent("SESSION_RESPONSE_REPEAT","high",no_execute=True)
        if phrase in {"всё понятно","все понятно"}: return ResolvedIntent("SESSION_ACKNOWLEDGEMENT","high",no_execute=True)
        if phrase in {"сделай это снова","повтори это","открой ещё раз","открой еще раз"}:
            return ResolvedIntent("UNKNOWN","high",reference_to_session=True,no_execute=True)
        if re.search(r"\b(?:нужен|нужна|открой|запусти)\b.*\bбраузер\b",phrase): return ResolvedIntent("OPEN_BROWSER","high")
        if re.search(r"\b(?:открой|запусти|включи)\b.*\b(?:youtube|ютуб)\b",phrase): return ResolvedIntent("OPEN_YOUTUBE","high")
        if "громк" in phrase or "звук" in phrase:
            return ResolvedIntent("INCOMPLETE_SET_VOLUME","medium",requires_clarification=True,no_execute=True)
        return ResolvedIntent()
