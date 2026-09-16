from core.plugin_api import AssistantPlugin
from core.models import Result
class Plugin(AssistantPlugin):
 def __init__(self, endpoint_factory=None): self._endpoint_factory=endpoint_factory
 def get_commands(self): return ["SET_VOLUME","VOLUME_UP","VOLUME_DOWN","MUTE","UNMUTE"]
 def _endpoint(self):
  if self._endpoint_factory: return self._endpoint_factory()
  from pycaw.pycaw import AudioUtilities
  return AudioUtilities.GetSpeakers().EndpointVolume
 def execute(self,c):
  try:
   e=self._endpoint()
   if c.intent=="SET_VOLUME": level=c.parameters["level"]; e.SetMasterVolumeLevelScalar(level/100,None); msg=f"Громкость {level}%."; data={"level":level,"composer_owned_template":"set_volume_success"} if isinstance(level,int) and 0<=level<=100 else {}
   elif c.intent=="VOLUME_UP": e.SetMasterVolumeLevelScalar(min(1,e.GetMasterVolumeLevelScalar()+.1),None); msg="Делаю громче."
   elif c.intent=="VOLUME_DOWN": e.SetMasterVolumeLevelScalar(max(0,e.GetMasterVolumeLevelScalar()-.1),None); msg="Делаю тише."
   else: e.SetMute(1 if c.intent=="MUTE" else 0,None); msg="Звук выключен." if c.intent=="MUTE" else "Звук включён."
   return Result(True,msg,data if c.intent=="SET_VOLUME" else {})
  except ImportError: return Result(False,"Для системной громкости установите зависимости из requirements.txt.")
