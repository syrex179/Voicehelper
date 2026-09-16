from .models import Result

class CommandRouter:
    def __init__(self,registry,plugins,permissions,skills,events=None): self.registry,self.plugins,self.permissions,self.skills,self.events=registry,plugins,permissions,skills,events
    def route(self,command,confirmed=False):
        if command.intent=="UNKNOWN":
            skill=self.skills.find(command.raw_text)
            return self.skills.run(skill["name"]) if skill else Result(False,"Я не понял команду.")
        denial=self.permissions.check(command,confirmed)
        if denial: return denial
        plugin_id=self.registry.plugin_for(command.intent)
        if not plugin_id: return Result(False,f"Команда {command.intent} недоступна.")
        try:
            result=self.plugins.execute(plugin_id,command)
            if self.events: self.events.emit("command_routed",command=command,result=result)
            return result
        except Exception as exc: return Result(False,f"Ошибка плагина {plugin_id}: {exc}")
