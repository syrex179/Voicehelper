"""Pure deterministic selection of a response type for one ConversationTurn."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ResponseDecision:
    response_type: str
    reason: str

class ResponsePolicy:
    _TERMINAL={"стоп","хватит","остановись","stop","пока","до свидания","до встречи"}
    _CANCEL={"отмена","отменить","отмени"}
    _CONTROL={"подожди","продолжай","слушай дальше","начнём сначала","начнем сначала","сбрось разговор"}
    _STATUS={"ты слушаешь","ты сейчас слушаешь","какой у тебя статус","ты на паузе"}
    _HELP={"что я могу сейчас сказать","что можно сейчас сделать","что ты сейчас от меня ждёшь","что ты сейчас от меня ждешь","как я могу с тобой сейчас говорить"}
    _SOCIAL={"SESSION_GREETING","SESSION_THANKS","SESSION_GOODBYE"}
    _INFORMATIONAL={"SESSION_RECAP","SHOW_ROUTINE","LIST_ROUTINES","PENDING_DIALOGUE_RECAP","ROUTINE_RECOMMENDATION_INFO"}

    def decide(self, turn):
        """Classify only snapshot data; never mutate or execute anything."""
        intent=turn.intent
        text=turn.normalized_text.strip("?.!")
        metadata=turn.action_result.get("metadata",{})
        executed_intent=metadata.get("executed_intent")
        if turn.action_result.get("success") and isinstance(executed_intent,str) and executed_intent and executed_intent!="UNKNOWN": intent=executed_intent
        provider_type=metadata.get("provider_response_type")
        if provider_type in {"conversational","informational"}: return ResponseDecision(provider_type,"validated_provider")
        if metadata.get("information_request"): return ResponseDecision("informational","information_provider")
        if text in self._TERMINAL or intent=="SESSION_GOODBYE": return ResponseDecision("social","terminal")
        if text in self._CANCEL or intent in {"CANCEL_PENDING_DIALOGUE","CANCEL_ROUTINE_EXECUTION"}: return ResponseDecision("informational","cancellation")
        if metadata.get("corrected"): return ResponseDecision("correction","correction")
        if turn.pending_state: return ResponseDecision("clarification","pending")
        if text in self._CONTROL or intent in {"SESSION_PAUSE","SESSION_RESUME","SESSION_RESET"}: return ResponseDecision("informational","session_control")
        if text in self._STATUS or intent=="SESSION_STATUS_QUERY": return ResponseDecision("status","status")
        if text in self._HELP or intent in {"HELP","SESSION_CONTEXTUAL_HELP"}: return ResponseDecision("help","help")
        if metadata.get("reference_replay"): return ResponseDecision("reference_replay","reference")
        if intent=="UNKNOWN" or metadata.get("read_only_guidance"): return ResponseDecision("unknown","unknown")
        if intent in self._INFORMATIONAL: return ResponseDecision("informational","informational")
        if intent in self._SOCIAL: return ResponseDecision("social","social")
        if intent=="SESSION_ACKNOWLEDGEMENT": return ResponseDecision("social","acknowledgement")
        if turn.action_result.get("action_type") or intent: return ResponseDecision("action_success" if turn.action_result.get("success") else "action_failure","action_result")
        return ResponseDecision("action_success" if turn.action_result.get("success") else "unknown","fallback")
