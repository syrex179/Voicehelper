"""JARVIS Windows assistant entry point. Run: python main.py"""
import logging
from config import LOG_DIR
from core.assistant import Assistant
from speech.text_to_speech import TextToSpeech
from ui.main_window import MainWindow

def build_assistant():
    """Create the desktop assistant with its existing local conversation path.

    Enabling the local adapter only configures the localhost transport; the
    first ordinary conversational turn remains the first actual Ollama request.
    """
    assistant=Assistant()
    assistant.enable_ollama_provider()
    assistant.start()
    return assistant

def main():
    LOG_DIR.mkdir(exist_ok=True); logging.basicConfig(filename=LOG_DIR/"assistant.log",level=logging.INFO)
    assistant=build_assistant(); settings=assistant.memory.settings
    MainWindow(assistant,TextToSpeech(settings["tts_rate"],settings["tts_volume"],settings.get("tts_enabled",True),lambda state: assistant.set_state(getattr(__import__("core.models",fromlist=["AssistantState"]).AssistantState,state)))).run_loop()
if __name__=="__main__": main()
