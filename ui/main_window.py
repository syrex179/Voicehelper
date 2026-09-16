import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from speech.speech_to_text import SpeechToText
from speech.wake_word import WakeWord
from speech.microphone import MicrophoneManager
from speech.voice_controller import VoiceController
from core.models import AssistantState
from .plugin_manager_window import PluginManagerWindow
from .settings_window import SettingsWindow
from .skill_manager_window import SkillManagerWindow
from .tray import Tray
from .search_results_window import SearchResultsWindow

class MainWindow:
    def __init__(self, assistant, tts=None):
        self.assistant,self.tts=assistant,tts; self.root=tk.Tk(); self.root.title("JARVIS — Personal Assistant"); self.root.geometry("760x580"); self.root.minsize(620,480)
        self.status=tk.StringVar(value="READY"); self.command=tk.StringVar(); self.microphone_enabled=False; self._build(); assistant.events.subscribe("state_changed",self._state); assistant.events.subscribe("routine_progress",self._routine_progress)
        settings=assistant.memory.settings; self.microphone=MicrophoneManager(settings); self.voice=VoiceController(assistant,SpeechToText(settings.get("language","ru-RU"),settings.get("silence_timeout",5),settings.get("stt_backend","google"),settings.get("local_stt_model_path","") or None,settings.get("stt_fallback_to_google",False)),WakeWord(settings.get("wake_word","джарвис"),settings.get("wake_word_enabled",True)),self.microphone,tts,self._voice_result) if tts else None
        self.root.protocol("WM_DELETE_WINDOW",self.hide); self.tray=Tray(self); self.tray.start()
    def _build(self):
        root=self.root; root.configure(bg="#101622")
        tk.Label(root,text="JARVIS",font=("Segoe UI",28,"bold"),fg="#58d6ff",bg="#101622").pack(pady=(18,0))
        tk.Label(root,textvariable=self.status,font=("Segoe UI",11),fg="#a8b4c8",bg="#101622").pack(pady=(0,12))
        bar=tk.Frame(root,bg="#101622"); bar.pack(fill="x",padx=28)
        entry=ttk.Entry(bar,textvariable=self.command,font=("Segoe UI",12)); entry.pack(side="left",fill="x",expand=True); entry.bind("<Return>",lambda _:self.run())
        ttk.Button(bar,text="Выполнить",command=self.run).pack(side="left",padx=7)
        self.listen_button=ttk.Button(bar,text="🎙 Слушать",command=self.listen); self.listen_button.pack(side="left")
        ttk.Button(bar,text="⏹ Stop voice",command=lambda:self.tts.stop() if self.tts else None).pack(side="left",padx=4)
        self.output=tk.Text(root,height=9,wrap="word",bg="#192233",fg="#edf4ff",insertbackground="white",relief="flat",font=("Segoe UI",10)); self.output.pack(fill="x",padx=28,pady=14)
        tk.Label(root,text="РЕЖИМЫ",font=("Segoe UI",12,"bold"),fg="#a8b4c8",bg="#101622").pack(anchor="w",padx=28)
        self.cards=tk.Frame(root,bg="#101622"); self.cards.pack(fill="both",expand=True,padx=28,pady=8)
        actions=tk.Frame(root,bg="#101622"); actions.pack(pady=(0,14))
        ttk.Button(actions,text="Создать режим",command=self.create_mode).pack(side="left",padx=3)
        ttk.Button(actions,text="Skills",command=self.open_skills).pack(side="left",padx=3)
        ttk.Button(actions,text="Plugins",command=self.open_plugins).pack(side="left",padx=3)
        ttk.Button(actions,text="Settings",command=self.open_settings).pack(side="left",padx=3); self.refresh_modes()
    def _state(self,state):
        value=state.value if hasattr(state,"value") else state
        labels={"READY":"● Готов", "LISTENING":"● Слушаю…", "PROCESSING":"● Обрабатываю…", "SPEAKING":"● Говорю…", "ERROR":"● Ошибка"}
        self.root.after(0,lambda:self.status.set(labels.get(value,value)))
    def _routine_progress(self,message,**_):
        self.root.after(0,lambda:self.log("JARVIS: "+message))
        if self.tts: self.tts.say(message)
    def log(self,text): self.output.insert("end",text+"\n"); self.output.see("end")
    def run(self):
        text=self.command.get().strip()
        if not text:return
        self.process_text(text); self.command.set("")
    def process_text(self,text):
        result=self.assistant.handle(text)
        if result.confirmation_required and messagebox.askyesno("Подтверждение",result.message):
            result=self.assistant.handle("да") if self.assistant.dialogue.pending else self.assistant.handle(text,confirmed=True)
        self.log(f"Вы: {text}\nJARVIS: {result.message}"); self.command.set("")
        if self.tts:self.tts.say(result.message)
        if result.data.get("results"): SearchResultsWindow(self.root,result.data["results"])
        self.refresh_modes()
    def listen(self):
        if not self.voice: return
        if self.voice.listening: self.disable_microphone(); return
        self.enable_microphone()
    def enable_microphone(self):
        if self.voice and self.voice.start(): self.microphone_enabled=True; self.listen_button.configure(text="■ Остановить"); self.log("Микрофон включён.")
    def disable_microphone(self):
        if self.voice: self.voice.stop()
        self.microphone_enabled=False; self.listen_button.configure(text="🎙 Слушать"); self.log("Микрофон выключен.")
    def _voice_result(self,text,result): self.root.after(0,lambda:self._show_voice_result(text,result))
    def _show_voice_result(self,text,result):
        self.log((f"Вы: {text}\n" if text else "")+"JARVIS: "+result.message); self.refresh_modes()
    def create_mode(self):
        name=simpledialog.askstring("Новый режим","Название режима:",parent=self.root)
        if name:
            explanation=simpledialog.askstring("Действия","Например: открой Discord и Telegram, громкость 60",parent=self.root) or ""
            draft=self.assistant.learning.draft(explanation); draft["name"]=name; draft["trigger"]=name.lower()
            result=self.assistant.learning.create_from_explanation(explanation) if not draft["actions"] else None
            if draft["actions"]: self.assistant.skills.create(**draft); self.log(f"Режим «{name}» создан.")
            elif result: self.log(result.message)
            self.refresh_modes()
    def refresh_modes(self):
        for child in self.cards.winfo_children(): child.destroy()
        for i,skill in enumerate(self.assistant.memory.skills):
            card=tk.Frame(self.cards,bg="#21304a",padx=12,pady=10); card.grid(row=i//3,column=i%3,sticky="nsew",padx=5,pady=5)
            tk.Label(card,text=skill["icon"],font=("Segoe UI Emoji",27),bg="#21304a",fg="white").pack()
            tk.Label(card,text=skill["name"].upper(),font=("Segoe UI",10,"bold"),bg="#21304a",fg="white").pack()
            tk.Label(card,text=" • ".join(a["intent"].replace("_"," ") for a in skill["actions"]),wraplength=180,bg="#21304a",fg="#c8d8ed").pack(pady=4)
            ttk.Button(card,text="Запустить",command=lambda n=skill["name"]:self._run_skill(n)).pack()
        for column in range(3): self.cards.columnconfigure(column,weight=1)
    def _run_skill(self,name):
        result=self.assistant.skills.run(name); self.log("JARVIS: "+result.message)
        if self.tts: self.tts.say(result.message)
        self.refresh_modes()
    def open_plugins(self): PluginManagerWindow(self.root,self.assistant)
    def open_settings(self): SettingsWindow(self.root,self.assistant,self.open_plugins,self.tts)
    def open_skills(self): SkillManagerWindow(self.root,self.assistant,self.refresh_modes)
    def hide(self): self.root.withdraw(); self.log("JARVIS остаётся в системном трее.")
    def show(self): self.root.deiconify(); self.root.lift()
    def exit(self):
        self.disable_microphone();
        if self.tts: self.tts.shutdown()
        self.tray.stop(); self.root.destroy()
    def run_loop(self): self.root.mainloop()
