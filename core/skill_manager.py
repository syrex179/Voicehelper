import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from .models import Command, Result

class SkillManager:
    ICONS={"игр":"🎮","stream":"🎥","стрим":"🎥","музык":"🎵","работ":"💻","ноч":"🌙"}
    def __init__(self,memory,router): self.memory,self.router=memory,router
    def icon_for(self,name): return next((v for k,v in self.ICONS.items() if k in name.lower()),"⚡")
    def create(self,name,actions,trigger=None,description="",icon=None):
        now=datetime.now(timezone.utc).isoformat()
        skill={"id":str(uuid.uuid4()),"name":name,"trigger":trigger or name.lower(),"actions":actions,"description":description,"icon":icon or self.icon_for(name),"version":1,"created_at":now,"updated_at":now,"last_run_at":None}
        self.memory.skills.append(skill); self.memory.save_skills(); return skill
    def find(self, name): return next((x for x in self.memory.skills if x["name"].lower()==name.lower() or x["trigger"].lower()==name.lower()),None)
    def run(self,name):
        skill=self.find(name)
        if not skill: return Result(False,"Режим не найден.")
        results=[self.router.route(Command(a["intent"],a.get("parameters",{}),name), confirmed=True) for a in skill["actions"]]
        skill["last_run_at"]=datetime.now(timezone.utc).isoformat(); self.memory.save_skills()
        return Result(all(x.ok for x in results), f"Режим «{skill['name']}» выполнен.",{"results":[x.message for x in results]})
    def delete(self,name):
        skill=self.find(name)
        if not skill: return False
        self.memory.skills.remove(skill); self.memory.save_skills(); return True
    def update(self,name, **changes):
        skill=self.find(name)
        if not skill: return None
        allowed={"name","trigger","actions","description","icon"}
        skill.update({key:value for key,value in changes.items() if key in allowed and value is not None})
        skill["version"]=skill.get("version",0)+1; skill["updated_at"]=datetime.now(timezone.utc).isoformat()
        self.memory.save_skills(); return skill
    def duplicate(self,name, copy_name=None):
        source=self.find(name)
        if not source: return None
        return self.create(copy_name or f"{source['name']} copy", deepcopy(source["actions"]), description=source.get("description",""), icon=source.get("icon"))
    def export_skill(self,name, destination):
        skill=self.find(name)
        if not skill: return False
        Path(destination).write_text(json.dumps(skill,ensure_ascii=False,indent=2),encoding="utf-8"); return True
    def import_skill(self, source):
        payload=json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(payload,dict) or not isinstance(payload.get("actions"),list) or not payload.get("name"): raise ValueError("Некорректный файл Skill")
        name=payload["name"]
        if self.find(name): name=f"{name} (import)"
        return self.create(name,payload["actions"],payload.get("trigger",name.lower()),payload.get("description",""),payload.get("icon"))
