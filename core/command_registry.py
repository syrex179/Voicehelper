class CommandRegistry:
    def __init__(self): self._commands = {}
    def register(self, intent, plugin_id): self._commands[intent] = plugin_id
    def unregister_plugin(self, plugin_id):
        self._commands = {k:v for k,v in self._commands.items() if v != plugin_id}
    def plugin_for(self, intent): return self._commands.get(intent)
    def commands(self): return dict(self._commands)
