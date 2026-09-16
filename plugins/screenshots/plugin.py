from datetime import datetime
from pathlib import Path
from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 def get_commands(self): return ["TAKE_SCREENSHOT"]
 def execute(self,c):
  try:
   from PIL import ImageGrab
   folder=Path.home()/"Pictures"/"Screenshots"; folder.mkdir(parents=True,exist_ok=True)
   path=folder/("jarvis_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".png"); ImageGrab.grab().save(path)
   return Result(True,"Скриншот сохранён.",{"path":str(path)})
  except ImportError: return Result(False,"Для скриншотов установите Pillow из requirements.txt.")
