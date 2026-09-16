"""Manual, user-visible smoke for the optional Browser ChatGPT provider.

The launcher keeps only a small, safe result artifact. It never stores the
prompt, visible response text, browser storage, credentials, or exceptions.
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


# A direct `python tools/...py` launch makes `tools/` the import root. Keep
# this bootstrap local to the manual tool; production package imports stay put.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ARTIFACT_RELATIVE_PATH = Path("data/diagnostics/chatgpt_browser_smoke_result.json")
ARTIFACT_PATH = PROJECT_ROOT / ARTIFACT_RELATIVE_PATH
SAFE_RESULT_FIELDS = (
    "launcher_status", "browser_status", "login_status", "chatgpt_page",
    "request_sent", "response_received", "response_length", "provider_status",
    "router_calls", "plugin_calls", "memory_delta", "tts_calls",
    "final_status", "error_code",
    "input_candidates_count", "contenteditable_candidates_count", "textarea_candidates_count",
    "visible_input_candidates", "enabled_input_candidates", "send_candidates_count",
    "visible_send_candidates", "enabled_send_candidates", "active_element_type", "page_ready",
    "input_detected", "input_type", "input_fill_status", "send_control_detected",
    "send_control_type", "send_action_status",
    "candidate_count", "visible_count", "latest_role", "latest_length",
    "transition_status",
)
_TEXT_DEFAULTS = {
    "launcher_status": "NOT_STARTED", "browser_status": "NOT_AVAILABLE",
    "login_status": "NOT_AVAILABLE", "chatgpt_page": "NOT_AVAILABLE",
    "provider_status": "NOT_AVAILABLE", "final_status": "RUNTIME_FAILURE",
    "error_code": "", "active_element_type": "NOT_AVAILABLE",
    "input_type": "NOT_AVAILABLE", "input_fill_status": "NOT_ATTEMPTED",
    "send_control_type": "NOT_AVAILABLE", "send_action_status": "NOT_ATTEMPTED",
    "latest_role": "NOT_AVAILABLE", "transition_status": "NOT_OBSERVED",
}
_INTEGER_FIELDS = {"response_length", "router_calls", "plugin_calls", "memory_delta", "tts_calls"}
_INTEGER_FIELDS.update({"input_candidates_count", "contenteditable_candidates_count", "textarea_candidates_count", "visible_input_candidates", "enabled_input_candidates", "send_candidates_count", "visible_send_candidates", "enabled_send_candidates"})
_INTEGER_FIELDS.update({"candidate_count", "visible_count", "latest_length"})
_BOOLEAN_FIELDS = {"request_sent", "response_received", "page_ready", "input_detected", "send_control_detected"}
_EXIT_CODES = {
    "LOGIN_REQUIRED": 10,
    "CHATGPT_PAGE_NOT_FOUND": 11,
    "SEND_CONTROL_NOT_FOUND": 12,
    "RESPONSE_TIMEOUT": 13,
    "EMPTY_RESPONSE": 14,
    "BROWSER_CLOSED": 15,
    "PROVIDER_UNAVAILABLE": 16,
}
_SENSITIVE_VALUE = re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)", re.I)


def new_result():
    """Return the complete safe schema; callers cannot add arbitrary fields."""
    result = dict(_TEXT_DEFAULTS)
    result.update({field: False for field in _BOOLEAN_FIELDS})
    result.update({field: 0 for field in _INTEGER_FIELDS})
    return result


def safe_result(result):
    """Whitelist and type-normalize diagnostics before they can reach disk."""
    raw = dict(result or {})
    safe = new_result()
    for field in SAFE_RESULT_FIELDS:
        value = raw.get(field, safe[field])
        if field in _BOOLEAN_FIELDS:
            safe[field] = bool(value)
        elif field in _INTEGER_FIELDS:
            safe[field] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        else:
            text = str(value or "").upper()
            safe[field] = text if re.fullmatch(r"[A-Z_]{1,80}", text) and not _SENSITIVE_VALUE.search(text) else "NOT_AVAILABLE"
    return safe


def write_result_artifact(result, artifact_path=ARTIFACT_PATH):
    """Atomically persist only whitelisted scalar diagnostics."""
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(safe_result(result), ensure_ascii=False, indent=2) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(artifact_path.parent),
                                         prefix=f".{artifact_path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(artifact_path))
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return artifact_path


def print_summary(result):
    result = safe_result(result)
    print("=== CHATGPT_BROWSER_SMOKE_RESULT ===")
    print(f"artifact_path={ARTIFACT_RELATIVE_PATH.as_posix()}")
    for field in ("final_status", "browser_status", "login_status", "chatgpt_page",
                  "request_sent", "response_received", "response_length", "provider_status",
                  "router_calls", "plugin_calls", "memory_delta", "tts_calls",
                  "input_candidates_count", "contenteditable_candidates_count", "textarea_candidates_count",
                  "visible_input_candidates", "enabled_input_candidates", "send_candidates_count",
                  "visible_send_candidates", "enabled_send_candidates", "active_element_type", "page_ready",
                  "input_detected", "input_type", "input_fill_status", "send_control_detected",
                  "send_control_type", "send_action_status", "candidate_count",
                  "visible_count", "latest_role", "latest_length", "transition_status"):
        value = str(result[field]).lower() if isinstance(result[field], bool) else result[field]
        print(f"{field}={value}")
    print("=== END ===")


def exit_code_for(result):
    if result["final_status"] == "PASS":
        return 0
    return _EXIT_CODES.get(result["error_code"], 20)


def print_preflight(preflight):
    print("=== CHATGPT_BROWSER_PREFLIGHT ===")
    for field in ("browser_dependency", "playwright_package", "browser_binary",
                  "profile_directory", "transport_import", "engine_configuration",
                  "preflight_status"):
        print(f"{field}={preflight.get(field, 'NOT_AVAILABLE')}")
    print("=== END ===")


def record_provider_failure(result, provider_state):
    state = str(provider_state or "PROVIDER_UNAVAILABLE")
    result["provider_status"] = state
    result["final_status"] = state
    result["error_code"] = "PROVIDER_UNAVAILABLE" if state == "UNAVAILABLE" else state


def main():
    if "--import-check" in sys.argv:
        print("launcher_status=PASS core_import=PASS")
        return 0
    if "--preflight" in sys.argv:
        from core.browser_chatgpt_provider import BrowserChatGPTProvider
        preflight=BrowserChatGPTProvider().preflight()
        print_preflight(preflight)
        return 0 if preflight["preflight_status"]=="READY" else 16

    result = new_result()
    result["launcher_status"] = "STARTED"
    try:
        # Delayed imports ensure a launcher/runtime error still reaches the
        # artifact writer below without importing or changing production code.
        from core.assistant import Assistant
        from core.browser_chatgpt_provider import BrowserChatGPTProvider
        from speech.text_to_speech import TextToSpeech

        assistant = Assistant()
        provider = BrowserChatGPTProvider()
        assistant.browser_chatgpt_provider = provider
        router_calls = []
        assistant.events.subscribe("command_routed", lambda **_: router_calls.append(True))
        memory_before = len(assistant.memory.history)
        state = provider.status()
        result["browser_status"] = state
        result["login_status"] = "PASS" if state == "READY" else "LOGIN_REQUIRED" if state == "LOGIN_REQUIRED" else "NOT_AVAILABLE"
        result["chatgpt_page"] = "PASS" if state == "READY" else "NOT_AVAILABLE"
        if state != "READY":
            record_provider_failure(result, state)
            return exit_code_for(result)

        action_result = assistant.handle(
            "спроси ChatGPT: Ответь одним коротким предложением: что такое искусственный интеллект?"
        )
        dom_diagnostic=dict(provider.last_diagnostic or {})
        for field in ("input_candidates_count", "contenteditable_candidates_count", "textarea_candidates_count",
                      "visible_input_candidates", "enabled_input_candidates", "send_candidates_count",
                      "visible_send_candidates", "enabled_send_candidates", "active_element_type", "page_ready",
                      "input_detected", "input_type", "input_fill_status", "send_control_detected",
                      "send_control_type", "send_action_status"):
            result[field]=dom_diagnostic.get(field,result[field])
        result["candidate_count"]=dom_diagnostic.get("assistant_message_candidates",0)
        result["visible_count"]=dom_diagnostic.get("visible_assistant_messages",0)
        result["latest_role"]=dom_diagnostic.get("latest_message_role","NOT_AVAILABLE")
        result["latest_length"]=dom_diagnostic.get("latest_message_length",0)
        result["transition_status"]="OBSERVED" if dom_diagnostic.get("response_transition_observed") else "NOT_OBSERVED"
        response_length = len(action_result.message or "") if action_result.ok else 0
        result["request_sent"] = bool(dom_diagnostic.get("request_sent",False))
        result["response_received"] = bool(action_result.ok and response_length)
        result["response_length"] = response_length
        result["router_calls"] = len(router_calls)
        result["memory_delta"] = max(0, len(assistant.memory.history) - memory_before)
        result["plugin_calls"] = 0
        result["provider_status"] = provider.last_state
        if not action_result.ok or not response_length:
            record_provider_failure(result, provider.last_state)
            return exit_code_for(result)

        tts = TextToSpeech(rate=5)
        try:
            tts.say(action_result.message)
            result["tts_calls"] = 1
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (tts.is_speaking() or not tts._items.empty()):
                time.sleep(.05)
        finally:
            tts.shutdown()
        result["launcher_status"] = "PASS"
        result["final_status"] = "PASS"
        result["error_code"] = ""
        return 0
    except Exception:
        # Deliberately no exception object, traceback, text, or path is saved.
        result["final_status"] = "RUNTIME_FAILURE"
        result["error_code"] = "LAUNCHER_RUNTIME_FAILURE"
        return 20
    finally:
        try:
            write_result_artifact(result)
        finally:
            # stdout mirrors the artifact without response text or sensitive data.
            print_summary(result)


if __name__ == "__main__":
    sys.exit(main())
