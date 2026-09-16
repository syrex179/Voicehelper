import tkinter as tk
from tkinter import ttk
from core.autostart import WindowsAutostart
from speech.microphone import MicrophoneManager

class SettingsWindow(tk.Toplevel):
    def __init__(self,parent,assistant,plugin_opener,tts=None):
        super().__init__(parent); self.assistant,self.plugin_opener,self.tts=assistant,plugin_opener,tts; self.title("JARVIS — Settings"); self.geometry("520x500")
        settings=assistant.memory.settings
        self.wake_enabled=tk.BooleanVar(value=settings.get("wake_word_enabled",True)); self.wake_word=tk.StringVar(value=settings.get("wake_word","джарвис"))
        self.language=tk.StringVar(value=settings.get("language","ru-RU")); self.rate=tk.IntVar(value=settings.get("tts_rate",0)); self.volume=tk.IntVar(value=settings.get("tts_volume",100)); self.tts_enabled=tk.BooleanVar(value=settings.get("tts_enabled",True)); self.autostart=tk.BooleanVar(value=WindowsAutostart().enabled())
        self.stt_backend=tk.StringVar(value=settings.get("stt_backend","google")); self.local_stt_model_path=tk.StringVar(value=settings.get("local_stt_model_path","")); self.stt_fallback=tk.BooleanVar(value=settings.get("stt_fallback_to_google",False))
        self.microphones=MicrophoneManager(settings); devices=self.microphones.devices(); self.device_labels=["Default"]+[f"{d['index']}: {d['name']}" for d in devices]; selected=settings.get("microphone_index"); self.microphone=tk.StringVar(value="Default" if selected is None else next((x for x in self.device_labels if x.startswith(str(selected)+":")),"Default"))
        body=ttk.Frame(self,padding=16); body.pack(fill="both",expand=True)
        ttk.Label(body,text="VOICE",font=("Segoe UI",11,"bold")).grid(row=0,column=0,sticky="w",pady=(0,8))
        ttk.Label(body,text="Language").grid(row=1,column=0,sticky="w"); ttk.Combobox(body,textvariable=self.language,values=("ru-RU","uk-UA"),state="readonly").grid(row=1,column=1,sticky="ew")
        ttk.Label(body,text="Microphone").grid(row=2,column=0,sticky="w"); ttk.Combobox(body,textvariable=self.microphone,values=self.device_labels,state="readonly").grid(row=2,column=1,sticky="ew")
        ttk.Button(body,text="Test Microphone",command=self.test_microphone).grid(row=3,column=1,sticky="e",pady=3)
        ttk.Label(body,text="STT backend").grid(row=4,column=0,sticky="w"); ttk.Combobox(body,textvariable=self.stt_backend,values=("google","local"),state="readonly").grid(row=4,column=1,sticky="ew")
        ttk.Label(body,text="Local Vosk model path").grid(row=5,column=0,sticky="w"); ttk.Entry(body,textvariable=self.local_stt_model_path).grid(row=5,column=1,sticky="ew")
        ttk.Checkbutton(body,text="Fallback to Google if local unavailable",variable=self.stt_fallback).grid(row=6,column=0,columnspan=2,sticky="w")
        ttk.Label(body,text="TTS speed").grid(row=7,column=0,sticky="w"); ttk.Scale(body,from_=-10,to=10,variable=self.rate).grid(row=7,column=1,sticky="ew")
        ttk.Label(body,text="TTS volume").grid(row=8,column=0,sticky="w"); ttk.Scale(body,from_=0,to=100,variable=self.volume).grid(row=8,column=1,sticky="ew")
        ttk.Button(body,text="Test Voice",command=self.test_voice).grid(row=9,column=1,sticky="e",pady=3)
        ttk.Checkbutton(body,text="Enable TTS",variable=self.tts_enabled).grid(row=10,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(body,text="Start JARVIS with Windows",variable=self.autostart).grid(row=11,column=0,columnspan=2,sticky="w")
        ttk.Separator(body).grid(row=12,columnspan=2,sticky="ew",pady=14)
        ttk.Label(body,text="WAKE WORD",font=("Segoe UI",11,"bold")).grid(row=13,column=0,sticky="w")
        ttk.Checkbutton(body,text="Enable wake word",variable=self.wake_enabled).grid(row=14,column=0,columnspan=2,sticky="w")
        ttk.Entry(body,textvariable=self.wake_word).grid(row=15,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Button(body,text="Plugin Manager",command=self.plugin_opener).grid(row=16,column=0,sticky="w",pady=14)
        ttk.Button(body,text="Save",command=self.save).grid(row=16,column=1,sticky="e",pady=14)
        body.columnconfigure(1,weight=1)
    def save(self):
        enabled=self.autostart.get(); self.assistant.autostart.set_enabled(enabled)
        index=None if self.microphone.get()=="Default" else int(self.microphone.get().split(":",1)[0]); self.microphones.select(index)
        self.assistant.memory.settings.update({"wake_word_enabled":self.wake_enabled.get(),"wake_word":self.wake_word.get().strip() or "джарвис","language":self.language.get(),"stt_backend":self.stt_backend.get(),"local_stt_model_path":self.local_stt_model_path.get().strip(),"stt_fallback_to_google":self.stt_fallback.get(),"tts_enabled":self.tts_enabled.get(),"tts_rate":self.rate.get(),"tts_volume":self.volume.get(),"autostart":enabled})
        self.assistant.memory.save_settings(); self.destroy()
    def test_microphone(self):
        from tkinter import messagebox
        index=None if self.microphone.get()=="Default" else int(self.microphone.get().split(":",1)[0]); self.microphones.select(index); ok,message=self.microphones.test(); messagebox.showinfo("Microphone",message) if ok else messagebox.showerror("Microphone",message)
    def test_voice(self):
        from tkinter import messagebox
        if self.tts and self.tts.say("Тест голосового помощника JARVIS успешно выполнен."): return
        messagebox.showerror("Voice","TTS backend недоступен или выключен.")
