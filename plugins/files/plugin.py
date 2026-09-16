import os
from pathlib import Path
from datetime import datetime
from core.plugin_api import AssistantPlugin
from core.models import Result

class Plugin(AssistantPlugin):
    def initialize(self, context):
        super().initialize(context); self.last_results=[]
        memory=context.get("memory")
        if memory is None: self.settings={"roots":[],"max_results":50}; return
        settings=memory.settings.setdefault("plugins",{}).setdefault("files",{})
        home=Path.home(); settings.setdefault("roots",[str(home/"Documents"),str(home/"Downloads"),str(home/"Desktop")]); settings.setdefault("max_results",50)
        memory.save_settings()
    def get_commands(self): return ["OPEN_FOLDER","SEARCH_FILE","OPEN_FOUND_FILE","SHOW_FOUND_FOLDER"]
    def discover_resources(self,target):
        """Bounded discovery metadata; paths stay inside this plugin."""
        query=getattr(target,"normalized_name","")
        if target.resource_type=="folder":
            known={"загрузки":"Загрузки","документы":"Документы","рабочий стол":"Рабочий стол"}
            return [{"name":label,"aliases":[name],"source":"allowed_folder","command":{"intent":"OPEN_FOLDER","parameters":{"query":name}}} for name,label in known.items() if query==name]
        if target.resource_type!="file" or not query: return []
        found=[]
        for root in self._roots():
            try:
                for path in root.rglob("*"):
                    if len(found)>=int(self.context.get("memory").settings["plugins"]["files"].get("max_results",50)): break
                    if path.is_file() and query in path.name.casefold(): found.append({"name":path.name,"aliases":[],"source":"safe_file_root","command":{"intent":"SEARCH_FILE","parameters":{"query":query}}})
            except (OSError,PermissionError): continue
        return found
    def _roots(self):
        settings=self.context.get("memory").settings["plugins"]["files"] if self.context.get("memory") else self.settings
        return [Path(p) for p in settings.get("roots",[]) if Path(p).is_dir()]
    def execute(self, command):
        query=command.parameters.get("query",command.raw_text).lower(); home=Path.home(); folders={"загрузки":home/"Downloads","документы":home/"Documents","рабочий стол":home/"Desktop"}
        if command.intent=="OPEN_FOLDER":
            for title,path in folders.items():
                if title in query and path.exists(): os.startfile(path); return Result(True,f"Открываю {title}.")
            return Result(False,"Уточните папку: Загрузки, Документы или Рабочий стол.")
        if command.intent in {"OPEN_FOUND_FILE","SHOW_FOUND_FOLDER"}:
            index=command.parameters.get("index",0)
            if not isinstance(index,int) or not 0 <= index < len(self.last_results): return Result(False,"Выберите результат поиска.")
            path=Path(self.last_results[index]["path"]); os.startfile(path.parent if command.intent=="SHOW_FOUND_FOLDER" else path)
            return Result(True,"Открываю проводник." if command.intent=="SHOW_FOUND_FOLDER" else f"Открываю {path.name}.")
        term=query.replace("найди файл","").replace("найди все","").replace("по названию","").strip(" ."); extension=None
        if " pdf" in " "+term or term.endswith("pdf"): extension=".pdf"; term=term.replace("pdf","").strip()
        settings=self.context.get("memory").settings["plugins"]["files"] if self.context.get("memory") else self.settings
        results=[]; limit=int(settings.get("max_results",50))
        for root in self._roots():
            try:
                for path in root.rglob("*"):
                    if len(results)>=limit: break
                    if not path.is_file() or (term and term not in path.name.lower()) or (extension and path.suffix.lower()!=extension): continue
                    stat=path.stat(); results.append({"name":path.name,"path":str(path),"type":path.suffix or "file","size":stat.st_size,"modified":datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
            except (OSError,PermissionError): continue
            if len(results)>=limit: break
        self.last_results=results; return Result(bool(results),f"Найдено файлов: {len(results)}." if results else "Файлы не найдены.",{"results":results})
