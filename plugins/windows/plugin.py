import ctypes, subprocess, os
from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 def get_commands(self): return ["OPEN_SETTINGS","OPEN_TASK_MANAGER","SHOW_DESKTOP","LOCK","SHUTDOWN","RESTART"]
 def execute(self,c):
  if c.intent=="OPEN_SETTINGS": os.startfile("ms-settings:"); return Result(True,"Открываю настройки.")
  if c.intent=="OPEN_TASK_MANAGER": subprocess.Popen(["taskmgr.exe"]); return Result(True,"Открываю диспетчер задач.")
  if c.intent=="SHOW_DESKTOP": ctypes.windll.user32.keybd_event(0x5B,0,0,0); ctypes.windll.user32.keybd_event(0x44,0,0,0); ctypes.windll.user32.keybd_event(0x44,0,2,0); ctypes.windll.user32.keybd_event(0x5B,0,2,0); return Result(True,"Показываю рабочий стол.")
  if c.intent=="LOCK": ctypes.windll.user32.LockWorkStation(); return Result(True,"Компьютер заблокирован.")
  subprocess.Popen(["shutdown","/s" if c.intent=="SHUTDOWN" else "/r","/t","10"]); return Result(True,"Действие запланировано через 10 секунд.")
