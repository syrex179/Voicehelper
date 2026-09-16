from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict

class AssistantState(str, Enum):
    OFFLINE="OFFLINE"; READY="READY"; LISTENING="LISTENING"; PROCESSING="PROCESSING"
    EXECUTING="EXECUTING"; SPEAKING="SPEAKING"; LEARNING="LEARNING"; ERROR="ERROR"

@dataclass
class Command:
    intent: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    entities: Dict[str, Any] = field(default_factory=dict)
    actions: list = field(default_factory=list)

@dataclass
class Result:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    confirmation_required: bool = False
