"""Microphone discovery and short non-persistent input checks."""
class MicrophoneManager:
    def __init__(self, settings): self.settings=settings
    def devices(self):
        try:
            import speech_recognition as sr
            return [{"index":i,"name":name} for i,name in enumerate(sr.Microphone.list_microphone_names())]
        except Exception: return []
    def selected_index(self):
        value=self.settings.get("microphone_index")
        return value if isinstance(value,int) else None
    def select(self, index):
        if index is not None and not any(d["index"]==index for d in self.devices()): raise RuntimeError("Микрофон недоступен")
        self.settings["microphone_index"]=index
    def test(self, seconds=0.25):
        try:
            import speech_recognition as sr
            with sr.Microphone(device_index=self.selected_index()) as source:
                source.stream.read(max(256,int(source.SAMPLE_RATE*seconds)))
            return True,"Микрофон работает."
        except Exception as exc: return False,f"Не удалось получить аудио с микрофона: {exc}"
