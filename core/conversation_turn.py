"""Ephemeral, copy-safe structured data for one conversational turn."""
import re
from dataclasses import dataclass
from types import MappingProxyType

_SENSITIVE=re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)",re.I)

def _safe_mapping(value):
    if not isinstance(value,dict): return MappingProxyType({})
    safe={}
    for key,item in value.items():
        if _SENSITIVE.search(str(key)): continue
        if isinstance(item,(str,int,float,bool)) or item is None: safe[str(key)]=item
    return MappingProxyType(safe)

@dataclass(frozen=True)
class ConversationTurn:
    user_text: str
    normalized_text: str
    session_state: str
    intent: str
    response_type: str
    action_result: object
    pending_state: object
    reference_context: object
    timestamp: float
    source: str="voice"

    @classmethod
    def create(cls, *, user_text, session_state, intent="", response_type="", result=None, pending_state=None, reference_context=None, safe_metadata=None, timestamp=0.0, source="voice"):
        text=str(user_text or "").strip()
        normalized=" ".join(text.casefold().split())
        result_data=dict(getattr(result,"data",{}) or {}) if result is not None else {}
        result_data.update(dict(safe_metadata or {}))
        action={"success":bool(getattr(result,"ok",False)),"action_type":str(intent or ""),"message":str(getattr(result,"message","") or ""),"metadata":_safe_mapping(result_data)}
        return cls(text,normalized,str(session_state or "READY"),str(intent or ""),str(response_type or ""),MappingProxyType(action),_safe_mapping(pending_state),_safe_mapping(reference_context),float(timestamp),str(source))

    def to_dict(self):
        action=dict(self.action_result); action["metadata"]=dict(action.get("metadata",{}))
        return {"user_text":self.user_text,"normalized_text":self.normalized_text,"session_state":self.session_state,"intent":self.intent,"response_type":self.response_type,"action_result":action,"pending_state":dict(self.pending_state),"reference_context":dict(self.reference_context),"timestamp":self.timestamp,"source":self.source}
