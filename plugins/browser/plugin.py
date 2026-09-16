import os, shutil, urllib.parse, webbrowser
from pathlib import Path
from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 def __init__(self, launcher=None): self._launcher=launcher or os.startfile
 def get_commands(self): return ["OPEN_BROWSER","OPEN_YOUTUBE","SEARCH_YOUTUBE","SEARCH_WEB"]
 def _browser_executable(self):
  names=("msedge.exe","chrome.exe","firefox.exe")
  for name in names:
   found=shutil.which(name)
   if found: return found
  roots=(os.environ.get("PROGRAMFILES",""),os.environ.get("PROGRAMFILES(X86)",""),os.environ.get("LOCALAPPDATA","") )
  relative=("Microsoft/Edge/Application/msedge.exe","Google/Chrome/Application/chrome.exe","Mozilla Firefox/firefox.exe")
  for root in roots:
   for item in relative:
    candidate=Path(root)/item
    if candidate.is_file(): return str(candidate)
  return None
 def _open_url(self,url):
  parsed=urllib.parse.urlparse(url)
  if parsed.scheme not in {"http","https"} or not parsed.netloc: return Result(False,"Разрешены только URL http:// или https://.")
  try: webbrowser.open(url); return Result(True,"Открываю страницу в браузере.",{"url":url})
  except Exception as exc: return Result(False,f"Не удалось открыть URL: {exc}")
 def _launch_browser(self):
  executable=self._browser_executable()
  if not executable: return Result(False,"Не найден установленный браузер.")
  try: self._launcher(executable); return Result(True,"Открываю браузер.",{"executable":executable,"composer_owned_template":"open_browser_success"})
  except OSError as exc: return Result(False,f"Не удалось запустить браузер: {exc}")
 def execute(self,c):
  urls={"OPEN_YOUTUBE":"https://www.youtube.com"}
  if c.intent=="SEARCH_YOUTUBE": url="https://www.youtube.com/results?search_query="+urllib.parse.quote(c.parameters.get("query",""))
  elif c.intent=="SEARCH_WEB": url="https://www.google.com/search?q="+urllib.parse.quote(c.parameters.get("query",c.raw_text))
  elif c.intent=="OPEN_BROWSER":
   url=c.parameters.get("url")
   return self._open_url(url) if url else self._launch_browser()
  else: url=urls[c.intent]
  result=self._open_url(url)
  if c.intent=="OPEN_YOUTUBE" and result.ok:
   data=dict(result.data); data.update({"safe_target":"YouTube","composer_owned_template":"open_youtube_success"})
   return Result(True,result.message,data)
  return result
