from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"
PLUGIN_DIR = APP_ROOT / "plugins"
LOG_DIR = APP_ROOT / "logs"
DEFAULT_SETTINGS = {
    "wake_word": "джарвис", "wake_word_enabled": True, "continuous_listening": False,
    "language": "ru-RU", "tts_enabled": True, "stt_enabled": True, "stt_backend": "google", "local_stt_model_path": "", "stt_fallback_to_google": False, "auto_listen": False, "speech_timeout": 6, "silence_timeout": 5, "conversation_timeout": 30, "microphone_index": None, "tts_rate": 0, "tts_volume": 100, "autostart": False, "confirm_dangerous": True, "routine_history_limit": 100,
}
