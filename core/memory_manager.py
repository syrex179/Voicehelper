import json
from copy import deepcopy
from pathlib import Path
from config import DATA_DIR, DEFAULT_SETTINGS

class MemoryManager:
    def __init__(self, directory=DATA_DIR):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.settings = self._load("settings.json", DEFAULT_SETTINGS)
        self.skills = self._load("skills.json", [])
        self.aliases = self._load("aliases.json", {})
        self.custom_commands = self._load("custom_commands.json", {})
        self.learned_routines = self._load("learned_routines.json", [])
        self.routine_execution_history = self._load("routine_execution_history.json", [])
        self.routine_presets = self._load("routine_presets.json", [])
        self.scheduled_routines = self._load("scheduled_routines.json", [])
        self.history = self._load("history.json", [])
    def _load(self, name, default):
        path=self.directory/name
        try:
            with path.open(encoding="utf-8") as f: return json.load(f)
        except (OSError, json.JSONDecodeError): return deepcopy(default)
    def _save(self, name, value):
        path=self.directory/name; temp=path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f: json.dump(value, f, ensure_ascii=False, indent=2)
        temp.replace(path)
    def save_settings(self): self._save("settings.json", self.settings)
    def save_skills(self): self._save("skills.json", self.skills)
    def save_aliases(self): self._save("aliases.json", self.aliases)
    def save_custom_commands(self): self._save("custom_commands.json", self.custom_commands)
    def save_learned_routines(self): self._save("learned_routines.json", self.learned_routines)
    def save_routine_execution_history(self): self._save("routine_execution_history.json", self.routine_execution_history)
    def save_routine_presets(self): self._save("routine_presets.json", self.routine_presets)
    def save_scheduled_routines(self): self._save("scheduled_routines.json", self.scheduled_routines)
    def add_history(self, text, result):
        self.history.insert(0, {"text":text,"result":result})
        self.history=self.history[:200]; self._save("history.json", self.history)
