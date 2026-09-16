import queue
import re
import threading
import os
from pathlib import Path
import subprocess
import tempfile

class TextToSpeech:
    """Single-worker SAPI queue: no overlapping voices or UI blocking."""
    def __init__(self, rate=0, volume=100, enabled=True, on_state=None, voice_id=None):
        self.rate,self.volume,self.enabled,self.on_state,self.voice_id=rate,volume,enabled,on_state,voice_id
        self._items=queue.Queue(); self._stop=threading.Event(); self._speaking=threading.Event(); self._voice=None
        self._speech_started=threading.Event(); self._speech_finished=threading.Event()
        self.last_error=""; self._spoken_count=0; self._last_com_error={}; self._selected_voice_id=None
        root=Path(__file__).resolve().parents[1]
        self._piper_paths={
            "python":root / ".venv_piper" / "Scripts" / "python.exe",
            "bridge_root":Path("C:/piper_build/smoke_env"),
            "model":root / ".venv_piper" / "models" / "ru_RU-dmitri-medium.onnx",
            "espeak_data":Path("C:/piper_build/source/src/piper/espeak-ng-data"),
            "worker":Path(__file__).with_name("piper_synthesis_worker.py"),
        }
        self._last_backend="SAPI"
        self._worker=threading.Thread(target=self._run,daemon=True,name="jarvis-tts"); self._worker.start()
    def available_voices(self):
        pythoncom=None; initialized=False
        try:
            import pythoncom
            pythoncom.CoInitialize(); initialized=True
            import win32com.client
            voice=win32com.client.Dispatch("SAPI.SpVoice")
            return [{"id":item.Id,"name":item.GetDescription()} for item in voice.GetVoices()]
        except Exception: return []
        finally:
            if initialized and pythoncom is not None:
                try: pythoncom.CoUninitialize()
                except Exception: pass
    def say(self,text):
        if self.enabled and text: self._items.put(str(text)); return True
        return False
    def is_speaking(self): return self._speaking.is_set()
    def diagnostic_status(self):
        """Safe SAPI worker state; no queued text or exception detail is exposed."""
        return {"backend":"SAPI","tts_enabled":bool(self.enabled),"worker_started":self._worker.is_alive(),
                "speech_started":self._speech_started.is_set(),"speech_finished":self._speech_finished.is_set(),
                "spoken_count":self._spoken_count,"last_error_class":self.last_error or None,
                "last_com_error":dict(self._last_com_error),"selected_voice_id":self._selected_voice_id,
                "active_backend":self._last_backend}
    def stop(self):
        self._stop.set()
        while not self._items.empty():
            try: self._items.get_nowait()
            except queue.Empty: break
        # SpVoice is owned exclusively by the STA worker.  Calling it from
        # the UI thread crosses apartment boundaries and can trigger SAPI COM
        # failures; the worker observes _stop before its next queued item.
    def shutdown(self): self.stop(); self._items.put(None); self._worker.join(timeout=2)
    def _emit(self,state):
        if self.on_state: self.on_state(state)
    @staticmethod
    def _text_language(text):
        """Return only the bounded voice-routing classes supported locally."""
        value=str(text or "")
        if re.search(r"[іїєґІЇЄҐ]",value): return ""
        if re.search(r"[А-Яа-яЁё]",value): return "ru"
        if re.search(r"[A-Za-z]",value): return "en"
        return ""
    @staticmethod
    def _token_language(token):
        try: value=str(token.GetAttribute("Language") or "")
        except Exception: value=""
        parts=re.findall(r"[0-9A-Fa-f]+",value)
        languages=set()
        for part in parts:
            try: languages.add(int(part,16))
            except ValueError: pass
        try: description=str(token.GetDescription()).casefold()
        except Exception: description=""
        if 0x419 in languages or "russian" in description or "русск" in description: return "ru"
        if 0x409 in languages or "english" in description: return "en"
        return ""
    def _select_voice(self, voice, text=""):
        """Select an installed token in the worker STA, preferring text language."""
        voices=voice.GetVoices()
        if not voices.Count:
            raise RuntimeError("SAPI_NO_VOICES")
        configured=str(self.voice_id) if self.voice_id else ""
        candidates=[voices.Item(index) for index in range(voices.Count)]
        selected=None; language=self._text_language(text)
        if language:
            language_candidates=[candidate for candidate in candidates if self._token_language(candidate)==language]
            if language_candidates:
                selected=next((candidate for candidate in language_candidates if candidate.Id==configured),language_candidates[0])
        if configured:
            if selected is None:
                selected=next((candidate for candidate in candidates if candidate.Id==configured),None)
        if selected is None:
            selected=candidates[0]
        voice.Voice=selected
        self._selected_voice_id=selected.Id
    def _piper_available(self):
        paths=self._piper_paths
        return all(Path(paths[name]).is_file() for name in ("python","model","worker")) and Path(paths["bridge_root"]).is_dir() and Path(paths["espeak_data"]).is_dir()
    @staticmethod
    def _play_wav(path):
        import winsound
        winsound.PlaySound(str(path),winsound.SND_FILENAME)
    def _speak_piper(self,text):
        """Synthesize Russian text locally without importing Piper into Python 3.8."""
        if not self._piper_available(): return False
        paths=self._piper_paths; descriptor,output_path=tempfile.mkstemp(prefix="jarvis_piper_",suffix=".wav")
        os.close(descriptor); output=Path(output_path)
        try:
            environment=dict(os.environ); existing=environment.get("PYTHONPATH","")
            environment["PYTHONPATH"]=str(paths["bridge_root"])+(os.pathsep+existing if existing else "")
            completed=subprocess.run(
                [str(paths["python"]),str(paths["worker"]),"--model",str(paths["model"]),"--espeak-data",str(paths["espeak_data"]),"--output",str(output)],
                input=str(text),text=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=45,env=environment,check=False)
            if completed.returncode or not output.is_file() or output.stat().st_size<=44: return False
            self._play_wav(output); return True
        except (OSError,subprocess.SubprocessError):
            return False
        finally:
            try: output.unlink(missing_ok=True)
            except OSError: pass
    def _run(self):
        pythoncom=None; initialized=False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED); initialized=True
        except Exception as exc:
            self.last_error="COM_INITIALIZATION_"+type(exc).__name__.upper()
        try:
            while True:
                text=self._items.get()
                if text is None: return
                self._stop.clear(); self._speech_started.clear(); self._speech_finished.clear()
                try:
                    # Piper is an optional local Russian path.  It owns neither
                    # routing nor response generation; the existing queue still
                    # serializes one final response at a time.  Any failure falls
                    # through to the proven SAPI path below.
                    if self._text_language(text)=="ru":
                        self._speaking.set(); self._speech_started.set(); self._emit("SPEAKING")
                        if self._speak_piper(text):
                            self._last_backend="PIPER"; self._spoken_count+=1; self.last_error=""; self._last_com_error={}
                            continue
                        self._speaking.clear()
                    import win32com.client
                    self._last_backend="SAPI"
                    self._voice=win32com.client.Dispatch("SAPI.SpVoice"); self._voice.Rate=self.rate; self._voice.Volume=self.volume
                    self._select_voice(self._voice,text)
                    if not self._stop.is_set():
                        self._speaking.set(); self._speech_started.set(); self._emit("SPEAKING")
                        self._voice.Speak(text); self._spoken_count+=1; self.last_error=""; self._last_com_error={}
                except Exception as exc:
                    # Keep the failure observable without retaining speech text
                    # or a traceback.  COM fields identify only the API fault.
                    self.last_error="SAPI_SPEAK_"+type(exc).__name__.upper()
                    args=getattr(exc,"args",())
                    self._last_com_error={"hresult":getattr(exc,"hresult",args[0] if args else None),
                                          "exception_class":type(exc).__name__,
                                          "excepinfo":getattr(exc,"excepinfo",args[2] if len(args)>2 else None),
                                          "argerror":getattr(exc,"argerror",args[3] if len(args)>3 else None)}
                finally:
                    self._voice=None; self._speaking.clear(); self._speech_finished.set(); self._emit("READY")
        finally:
            if initialized and pythoncom is not None:
                try: pythoncom.CoUninitialize()
                except Exception: pass
