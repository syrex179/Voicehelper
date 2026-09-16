"""Read-only Windows runtime diagnostics for JARVIS.

This module deliberately does not capture audio, speak, start the GUI/tray, run
routines, or alter the project's MemoryManager.  Hardware checks are reported
as NOT TESTED unless a user explicitly performs them from the existing UI.
"""
import importlib.util
import platform
import sys
import tempfile
from pathlib import Path

from config import APP_ROOT
from core.assistant import Assistant
from core.memory_manager import MemoryManager
from core.routine_manager import RoutineManager
from core.schedule_manager import ScheduledRoutineManager
from core.information_provider import BraveWebSearchInformationSource
from core.models import AssistantState, Result
from speech.microphone import MicrophoneManager
from speech.voice_controller import VoiceController
from speech.wake_word import WakeWord
from speech.speech_to_text import LocalVoskSTTBackend


def _available(module):
    try: return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError): return False


class RuntimeDiagnostics:
    def __init__(self, memory=None, microphone=None):
        self.memory=memory or MemoryManager()
        self.microphone=microphone or MicrophoneManager(self.memory.settings)
    def _status(self, state, detail=""): return {"state":state,"detail":detail}
    def collect(self):
        devices=self.microphone.devices()
        selected=self.microphone.selected_index()
        selected_ok=selected is None or any(item.get("index")==selected for item in devices)
        dependencies={name:_available(module) for name,module in {"SpeechRecognition":"speech_recognition","PyAudio":"pyaudio","Vosk":"vosk","SAPI/pywin32":"win32com.client","pystray":"pystray","Pillow":"PIL"}.items()}
        local=LocalVoskSTTBackend(self.memory.settings.get("local_stt_model_path") or None).status()
        web_search=BraveWebSearchInformationSource().status()
        browser_chatgpt=Assistant().browser_chatgpt_provider_status()
        browser_preflight=browser_chatgpt["preflight"]
        report={
            "Python":self._status("PASS",f"{sys.version.split()[0]}, {platform.architecture()[0]}, {platform.system()}"),
            "Project root":self._status("PASS",str(Path(APP_ROOT))),
            "dependencies":self._status("PASS" if all(dependencies[name] for name in ("SpeechRecognition","PyAudio")) else "READY",str(dependencies)),
            "Microphone devices":self._status("PASS" if devices else "NOT TESTED",f"{len(devices)} device(s)"),
            "Selected microphone":self._status("PASS" if selected_ok and selected is not None else "NOT TESTED" if selected is None else "FAILED",str(selected)),
            "Audio stream":self._status("NOT TESTED","Read-only diagnostics do not capture audio."),
            "STT":self._status("READY" if dependencies["SpeechRecognition"] else "FAILED","Backend availability only; no external recognition request."),
            "Local STT":self._status("READY" if local["backend_installed"] and local["model_available"] else "NOT CONFIGURED",f"Vosk installed={local['backend_installed']}; model={local['model_path'] or 'not set'}"),
            "TTS":self._status("READY" if dependencies["SAPI/pywin32"] else "FAILED","Backend availability only; no speech is emitted."),
            "Wake word":self._status("READY" if bool(self.memory.settings.get("wake_word")) else "FAILED",str(self.memory.settings.get("wake_word",""))),
            "OpenAI provider":self._status("NOT CONFIGURED","Offline adapter only; credentials are not read or displayed by diagnostics."),
            "Web Search provider":self._status(web_search["state"],"api_key_present="+str(web_search["api_key_present"]).lower()),
            "Browser ChatGPT provider":self._status(browser_chatgpt["state"],"User-controlled browser session; diagnostics do not open it."),
            "Browser ChatGPT preflight":self._status(browser_preflight["preflight_status"],"; ".join(f"{key}={browser_preflight[key]}" for key in ("playwright_package","browser_binary","profile_directory","transport_import","engine_configuration"))),
            "UI":self._status("NOT TESTED","GUI initialization is not safe in a headless diagnostic."),
            "tray":self._status("NOT TESTED","Tray is not started by diagnostics."),
        }
        report["VoiceController"]=self._voice_lifecycle()
        report["Assistant"]=self._assistant_smoke()
        report["Safety"]=self._status("PASS","Read-only: no audio capture, TTS speech, routines, shell, or persistence changes.")
        return report
    def _voice_lifecycle(self):
        class StubAssistant:
            def __init__(self): self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.state=AssistantState.READY
            def set_state(self,state): self.state=state
            def handle(self,text): return Result(True,"ok")
        class StubStt:
            def listen_once(self,*_): raise RuntimeError("synthetic diagnostic input")
        class StubMic:
            def selected_index(self): return None
        class StubTts:
            def say(self,text): return bool(text)
            def is_speaking(self): return False
        controller=VoiceController(StubAssistant(),StubStt(),WakeWord("джарвис"),StubMic(),StubTts())
        first=controller.start(); duplicate=controller.start(); controller.stop(); restarted=controller.start(); controller.stop()
        return self._status("PASS" if first and not duplicate and restarted and not controller.listening else "FAILED","Synthetic lifecycle only; physical microphone is NOT TESTED.")
    def _assistant_smoke(self):
        # A temporary MemoryManager proves the real VoiceController → Assistant
        # path without touching the user's history or learned data.
        class SilentStt: pass
        class SilentMic:
            def selected_index(self): return None
        class SilentTts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,assistant.router,assistant.events); assistant.schedules=ScheduledRoutineManager(memory,assistant.routines); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            tts=SilentTts(); controller=VoiceController(assistant,SilentStt(),WakeWord("джарвис"),SilentMic(),tts)
            result=controller.process_transcript("джарвис, покажи мои процедуры")
            return self._status("PASS" if result and result.ok and tts.messages else "FAILED","Temporary-memory informational command.")


def format_report(report):
    return "\n".join(f"{name}: {item['state']}{(' — '+item['detail']) if item['detail'] else ''}" for name,item in report.items())


def main():
    print(format_report(RuntimeDiagnostics().collect()))


if __name__=="__main__": main()
