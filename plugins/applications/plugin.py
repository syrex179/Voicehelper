import os, shutil, subprocess, webbrowser
from difflib import get_close_matches
from pathlib import Path
from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 ALIASES={"телега":"telegram","телеграм":"telegram","дис":"discord","дискорд":"discord","стим":"steam","обс":"obs","калькулятор":"calculator","проводник":"explorer"}
 BUILTINS={"calculator":"calc.exe","калькулятор":"calc.exe","explorer":"explorer.exe","проводник":"explorer.exe","notepad":"notepad.exe","блокнот":"notepad.exe"}
 MAX_SHORTCUTS_PER_SOURCE=300
 def get_commands(self): return ["OPEN_APPLICATION"]
 def discover_resources(self,target):
  """Return safe names and Router commands only; never expose paths."""
  query=getattr(target,"normalized_name",""); query=self.ALIASES.get(query,query)
  if not query: return []
  entries=[]
  for name in self.BUILTINS:
   if query==name or query in name: entries.append({"name":name,"aliases":[name],"source":"application_registry","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}})
  for alias,name in self.ALIASES.items():
   if query==alias or query==name:
    entries.append({"name":name,"aliases":[alias,name],"source":"application_alias","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}})
  # Matching belongs to ResourceResolver. Keep a bounded trusted shortcut
  # catalog so semantic product-family names can match display names.
  for base in (Path.home()/"Desktop",Path(os.environ.get("APPDATA",""))/"Microsoft/Windows/Start Menu/Programs",Path(os.environ.get("PROGRAMDATA",""))/"Microsoft/Windows/Start Menu/Programs"):
   if not base.exists(): continue
   try:
    patterns=("*.lnk","*.url") if base==Path.home()/"Desktop" else ("*.lnk",)
    shortcuts=[]
    for pattern in patterns: shortcuts.extend(base.rglob(pattern))
    for shortcut in sorted(shortcuts,key=lambda item:item.name.casefold())[:self.MAX_SHORTCUTS_PER_SOURCE]:
     name=shortcut.stem.strip()
     if name: entries.append({"name":name,"aliases":[],"source":"desktop_shortcut" if base==Path.home()/"Desktop" else "start_menu_shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}})
   except (OSError,PermissionError): continue
  return entries
 def execute(self,c):
  query=c.parameters.get("application","").lower().strip(); query=self.ALIASES.get(query,query)
  if query in ("браузер","browser","google","гугл"): webbrowser.open("about:blank"); return Result(True,"Открываю браузер.")
  target=self.BUILTINS.get(query) or shutil.which(query) or shutil.which(query+".exe")
  if not target:
   candidates=[]
   for base in (Path(os.environ.get("APPDATA",""))/"Microsoft/Windows/Start Menu/Programs",Path(os.environ.get("PROGRAMDATA",""))/"Microsoft/Windows/Start Menu/Programs",Path.home()/"Desktop"):
    if base.exists():
     candidates += list(base.rglob("*.lnk"))
     if base==Path.home()/"Desktop": candidates += list(base.rglob("*.url"))
   names={p.stem.lower():p for p in candidates}; best=get_close_matches(query,list(names),n=1,cutoff=.55)
   target=str(names[best[0]]) if best else None
  if not target: return Result(False,f"Не нашёл приложение «{c.parameters.get('application')}».",{"resource_not_found":True})
  try: os.startfile(str(target)); return Result(True,f"Открываю {c.parameters.get('application')}.")
  except OSError as e: return Result(False,f"Не удалось открыть приложение: {e}")
