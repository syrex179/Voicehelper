"""One-phrase, user-audible SAPI smoke for the existing JARVIS TTS worker.

Run manually from the project root.  This does not construct Assistant,
VoiceController, STT, Router, or any browser provider.
"""
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DEFAULT_SETTINGS
from speech.text_to_speech import TextToSpeech


def _settings():
    """Read only the existing public TTS settings; never persist anything."""
    path = PROJECT_ROOT / "data" / "settings.json"
    try:
        with path.open(encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        saved = {}
    return {
        "tts_enabled": bool(saved.get("tts_enabled", DEFAULT_SETTINGS["tts_enabled"])),
        "tts_rate": saved.get("tts_rate", DEFAULT_SETTINGS["tts_rate"]),
        "tts_volume": saved.get("tts_volume", DEFAULT_SETTINGS["tts_volume"]),
    }


def main():
    settings = _settings()
    states = []
    tts = TextToSpeech(
        rate=settings["tts_rate"], volume=settings["tts_volume"],
        enabled=settings["tts_enabled"], on_state=states.append,
    )
    try:
        voices = tts.available_voices()
        queue_accepted = tts.say("Проверка голосового вывода JARVIS.")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            status = tts.diagnostic_status()
            if status["speech_finished"] or status["last_error_class"]:
                break
            time.sleep(0.05)
        status = tts.diagnostic_status()
        print("=== MANUAL_PHYSICAL_TTS_SMOKE ===")
        print("tts_enabled=" + str(status["tts_enabled"]).lower())
        print("worker_started=" + str(status["worker_started"]).lower())
        print("sapi_available=" + str(bool(voices)).lower())
        print("voice_count=" + str(len(voices)))
        print("voice_selection=" + ("WINDOWS_DEFAULT" if tts.voice_id is None else "CONFIGURED"))
        print("queue_accepted=" + str(queue_accepted).lower())
        print("speech_started=" + str(status["speech_started"]).lower())
        print("speech_finished=" + str(status["speech_finished"]).lower())
        print("last_error_class=" + str(status["last_error_class"] or "NONE"))
        print("state_events=" + ",".join(state for state in states if state in {"SPEAKING", "READY"}))
        print("=== END ===")
        return 0 if queue_accepted and status["speech_started"] and status["speech_finished"] and not status["last_error_class"] else 1
    finally:
        tts.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
