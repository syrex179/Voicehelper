"""Pure bounded coordination metadata for a short voice-turn sequence."""
from dataclasses import dataclass

@dataclass(frozen=True)
class SequenceSnapshot:
    depth: int
    previous_turn_type: str
    current_dialogue_stage: str
    pending_kind: str=""
    pending_step: str=""
    last_safe_action_type: str=""
    last_response_type: str=""
    active: bool=True
    def to_dict(self): return {"depth":self.depth,"previous_turn_type":self.previous_turn_type,"current_dialogue_stage":self.current_dialogue_stage,"pending_kind":self.pending_kind,"pending_step":self.pending_step,"last_safe_action_type":self.last_safe_action_type,"last_response_type":self.last_response_type,"active":self.active}

class TurnSequenceCoordinator:
    MAX_DEPTH=3
    _TERMINAL={"стоп","хватит","остановись","stop","пока","до свидания","до встречи","отмена","отменить","отмени"}
    _PRESERVE={"ты слушаешь","ты сейчас слушаешь","какой у тебя статус","ты на паузе","что я могу сейчас сказать","что можно сейчас сделать","что ты сейчас от меня ждёшь","что ты сейчас от меня ждешь","как я могу с тобой сейчас говорить"}
    def advance(self, current, turn):
        text=turn.normalized_text.strip("?.!")
        if text in self._TERMINAL: return None
        pending=dict(turn.pending_state)
        if current and text in self._PRESERVE: return current
        if pending:
            depth=1 if current is None else current.depth+1
            if depth>=self.MAX_DEPTH: return None
            return SequenceSnapshot(depth,turn.response_type,"pending",str(pending.get("kind", "")),str(pending.get("step", "")),str(turn.reference_context.get("last_safe_action_type", "")),turn.response_type)
        if current is None and turn.response_type=="action_success" and turn.action_result.get("action_type"):
            return SequenceSnapshot(1,turn.response_type,"action","","",str(turn.action_result.get("action_type", "")),turn.response_type)
        return None
