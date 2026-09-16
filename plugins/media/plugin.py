import ctypes
from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 KEYS={"PLAY_PAUSE":0xB3,"NEXT_TRACK":0xB0,"PREVIOUS_TRACK":0xB1}
 def get_commands(self): return list(self.KEYS)
 def execute(self,c):
  key=self.KEYS[c.intent]; ctypes.windll.user32.keybd_event(key,0,0,0); ctypes.windll.user32.keybd_event(key,0,2,0); return Result(True,"Команда мультимедиа отправлена.")
