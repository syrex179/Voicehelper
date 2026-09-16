"""One-shot production VoiceController -> SAPI diagnostic; no microphone or UI."""
import contextlib
import io
import re
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.assistant import Assistant
from speech.text_to_speech import TextToSpeech
from speech.voice_controller import VoiceController
from speech.wake_word import WakeWord


UTTERANCE = "Джарвис, кто ты?"
_SENSITIVE = re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)", re.I)


class _Microphone:
    def selected_index(self):
        return None


def _safe_text(value):
    text = value if isinstance(value, str) else ""
    if _SENSITIVE.search(text):
        return "REDACTED"
    # The diagnostic must be durable in a terminal: never let a provider's
    # unbounded response make the one-shot result itself unreadable.
    normalized = " ".join(text.split())
    return normalized[:400] + ("…" if len(normalized) > 400 else "")


def _compact_com_error(value):
    if not isinstance(value, dict) or not value:
        return ""
    fields = ("hresult", "exception_class", "excepinfo", "argerror")
    rendered = ";".join(field + "=" + str(value.get(field)) for field in fields)
    return rendered[:500]


def main():
    assistant = None
    tts = None
    observed = {"enqueue_invoked": False, "queue_accepted": False, "tts_text": ""}
    status = {"worker_started": False, "speech_started": False, "spoken_count": 0,
              "speech_finished": False, "last_error_class": "RUNTIME_NOT_STARTED"}
    final_state = "NOT_STARTED"
    try:
        # Provider internals may emit verbose logs.  They are deliberately
        # suppressed so this launcher has exactly one compact result.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assistant = Assistant()
            assistant.enable_ollama_provider()
            assistant.start()
            settings = assistant.memory.settings
            tts = TextToSpeech(settings.get("tts_rate", 0), settings.get("tts_volume", 100), settings.get("tts_enabled", True))
            original_say = tts.say

            def traced_say(text):
                observed["enqueue_invoked"] = True
                observed["tts_text"] = text if isinstance(text, str) else ""
                accepted = original_say(text)
                observed["queue_accepted"] = bool(accepted)
                return accepted

            tts.say = traced_say
            voice = VoiceController(assistant, None, WakeWord(settings.get("wake_word", "джарвис"), settings.get("wake_word_enabled", True)), _Microphone(), tts)
            voice.process_transcript(UTTERANCE)
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                status = tts.diagnostic_status()
                if status["speech_finished"] or status["last_error_class"]:
                    break
                time.sleep(0.05)
            status = tts.diagnostic_status()
            final_state = "READY" if status["speech_finished"] and not status["last_error_class"] else "ERROR"
    except Exception as error:
        status["last_error_class"] = type(error).__name__
        final_state = "RUNTIME_ERROR"
    finally:
        if tts is not None:
            tts.shutdown()

    print("enqueue_tts=" + str(observed["enqueue_invoked"]).lower())
    print("response_text=" + _safe_text(observed["tts_text"]))
    print("queue_accepted=" + str(observed["queue_accepted"]).lower())
    print("worker_started=" + str(status["worker_started"]).lower())
    print("selected_voice_id=" + str(status.get("selected_voice_id") or "NONE"))
    print("speaking=" + str(status["speech_started"]).lower())
    print("sapi_speak=" + str(status["spoken_count"] > 0).lower())
    error = str(status["last_error_class"] or "NONE")
    com_error = _compact_com_error(status.get("last_com_error"))
    print("last_error=" + error + (";" + com_error if com_error else ""))
    print("final_state=" + final_state)
    return 0 if observed["queue_accepted"] and status["speech_started"] and status["speech_finished"] and not status["last_error_class"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
