"""Speech-to-text backends sharing the VoiceController listen_once interface."""
import importlib.util
import json
import re
from pathlib import Path

class STTUnavailable(RuntimeError): pass

class GoogleSTTBackend:
    name="google"
    def transcribe(self,audio,language):
        import speech_recognition as sr
        try: return sr.Recognizer().recognize_google(audio,language=language).strip()
        except Exception as exc: raise RuntimeError(f"Ошибка Google STT: {exc}")

class LocalVoskSTTBackend:
    """Optional Vosk backend. Audio stays in memory and is never written to disk."""
    name="local"
    def __init__(self,model_path=None,vosk_module=None):
        self.model_path=Path(model_path).expanduser() if model_path else None; self._vosk,self._model=vosk_module,None
    def status(self):
        installed=self._vosk is not None or importlib.util.find_spec("vosk") is not None
        model=bool(self.model_path and self.model_path.is_dir())
        return {"backend_installed":installed,"model_available":model,"model_path":str(self.model_path) if self.model_path else ""}
    def initialize(self):
        status=self.status()
        if not status["backend_installed"]: raise STTUnavailable("Локальный Vosk backend не установлен.")
        if not status["model_available"]: raise STTUnavailable("Локальная Vosk model не настроена или путь недоступен.")
        try:
            if self._vosk is None: import vosk as module; self._vosk=module
            self._model=self._vosk.Model(str(self.model_path)); return self
        except Exception as exc: raise STTUnavailable(f"Не удалось инициализировать локальную Vosk model: {exc}")
    @staticmethod
    def _safe_grammar(grammar):
        """Return a bounded Vosk grammar made only from trusted local hints."""
        if not isinstance(grammar,(list,tuple)): return None
        items=[]; seen=set()
        for value in grammar:
            if not isinstance(value,str): continue
            phrase=" ".join(value.split()).strip()
            if not phrase or len(phrase)>80 or not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ .-]+",phrase): continue
            key=phrase.casefold()
            if key not in seen: seen.add(key); items.append(phrase)
            if len(items)>=256: break
        return items or None
    def transcribe(self,audio,language,grammar=None):
        if self._model is None: self.initialize()
        try:
            hints=self._safe_grammar(grammar)
            recognizer=(self._vosk.KaldiRecognizer(self._model,16000,json.dumps(hints,ensure_ascii=False)) if hints else self._vosk.KaldiRecognizer(self._model,16000))
            recognizer.AcceptWaveform(audio.get_raw_data(convert_rate=16000,convert_width=2))
            return json.loads(recognizer.FinalResult()).get("text","").strip()
        except STTUnavailable: raise
        except Exception as exc: raise RuntimeError(f"Ошибка локального Vosk STT: {exc}")

class SpeechToText:
    """Backward-compatible facade; defaults remain Google STT."""
    # Capture tuning is deliberately kept here, before any local/cloud backend.
    # A longer, bounded calibration gives dynamic thresholding a stable ambient
    # baseline without changing the established listen/stop timeouts.
    AMBIENT_CALIBRATION_SECONDS=0.8
    DYNAMIC_ENERGY_DAMPING=0.5
    MINIMUM_SPEECH_SECONDS=0.2
    def __init__(self,language="ru-RU",silence_timeout=5,backend="google",local_model_path=None,fallback_to_google=False,backend_impl=None):
        self.language,self.silence_timeout=language,silence_timeout; self.backend=backend if backend in {"google","local"} else "google"; self.fallback_to_google=bool(fallback_to_google)
        self._google=GoogleSTTBackend(); self._local=backend_impl or LocalVoskSTTBackend(local_model_path); self._context_grammar=None
    def backend_status(self):
        if self.backend=="google": return {"backend":"google","available":importlib.util.find_spec("speech_recognition") is not None}
        status=self._local.status(); status.update({"backend":"local","available":status["backend_installed"] and status["model_available"]}); return status
    @staticmethod
    def numeric_grammar():
        """The complete, bounded Russian 0–100 vocabulary for a pending volume value."""
        units=("ноль","один","два","три","четыре","пять","шесть","семь","восемь","девять")
        teens=("десять","одиннадцать","двенадцать","тринадцать","четырнадцать","пятнадцать","шестнадцать","семнадцать","восемнадцать","девятнадцать")
        tens=("", "", "двадцать","тридцать","сорок","пятьдесят","шестьдесят","семьдесят","восемьдесят","девяносто")
        words=[]
        for value in range(101):
            if value<10: word=units[value]
            elif value<20: word=teens[value-10]
            elif value==100: word="сто"
            else: word=tens[value//10]+(" "+units[value%10] if value%10 else "")
            words.extend((str(value),word))
        return tuple(words)
    @staticmethod
    def _sanitize_hints(values):
        return tuple(LocalVoskSTTBackend._safe_grammar(values) or ())
    def set_context_hints(self,kind="",candidates=()):
        """One-turn Vosk hints; callers supply only existing pending-dialogue context."""
        if kind=="numeric": self._context_grammar=self.numeric_grammar()
        elif kind=="resource": self._context_grammar=self._sanitize_hints(candidates)
        else: self._context_grammar=None
    def clear_context_hints(self): self._context_grammar=None
    @property
    def context_hints_active(self): return bool(self._context_grammar)
    def _transcribe(self,audio):
        if self.backend=="google": return self._google.transcribe(audio,self.language)
        grammar=self._context_grammar
        try:
            if grammar and isinstance(self._local,LocalVoskSTTBackend): return self._local.transcribe(audio,self.language,grammar=grammar)
            return self._local.transcribe(audio,self.language)
        except STTUnavailable:
            if self.fallback_to_google: return self._google.transcribe(audio,self.language)
            raise
    @classmethod
    def _configure_capture_recognizer(cls,recognizer):
        """Apply bounded microphone VAD tuning without changing backend semantics."""
        recognizer.dynamic_energy_threshold=True
        recognizer.dynamic_energy_adjustment_damping=cls.DYNAMIC_ENERGY_DAMPING
        # Keep brief, quiet starts rather than discarding them as sub-phrases.
        recognizer.phrase_threshold=cls.MINIMUM_SPEECH_SECONDS
        return recognizer
    def listen_once(self,device_index=None,timeout=6):
        try:
            import speech_recognition as sr
            recognizer=self._configure_capture_recognizer(sr.Recognizer())
            with sr.Microphone(device_index=device_index) as source:
                recognizer.adjust_for_ambient_noise(source,duration=self.AMBIENT_CALIBRATION_SECONDS); audio=recognizer.listen(source,timeout=timeout,phrase_time_limit=self.silence_timeout)
            text=self._transcribe(audio)
            if not text: raise RuntimeError("Распознанная фраза пуста.")
            return text
        except STTUnavailable: raise
        except ImportError: raise RuntimeError("SpeechRecognition и PyAudio не установлены.")
        except RuntimeError: raise
        except Exception as exc: raise RuntimeError(f"Ошибка распознавания: {exc}")
        finally: self.clear_context_hints()
