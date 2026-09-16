import importlib.util, json, logging
from pathlib import Path
from config import PLUGIN_DIR

class PluginManager:
    def __init__(self, registry, context, directory=PLUGIN_DIR):
        self.registry, self.context, self.directory = registry, context, Path(directory)
        self.plugins, self.manifests, self.errors = {}, {}, {}
    def discover(self):
        return [p.parent for p in self.directory.glob("*/manifest.json")]
    def load_all(self):
        for folder in self.discover(): self.load(folder.name)
    def load(self, plugin_id):
        if plugin_id in self.plugins: return True
        folder=self.directory/plugin_id
        try:
            manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
            for key in ("id","name","version","description","enabled"): assert key in manifest, f"manifest: {key}"
            self.manifests[plugin_id]=manifest
            if not manifest["enabled"]: return False
            spec=importlib.util.spec_from_file_location(f"jarvis_plugin_{plugin_id}", folder/"plugin.py")
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            plugin=module.Plugin(); plugin.initialize(self.context)
            for intent in plugin.get_commands(): self.registry.register(intent, plugin_id)
            self.plugins[plugin_id]=plugin; self.errors.pop(plugin_id, None); return True
        except Exception as exc:
            self.errors[plugin_id]=str(exc); logging.exception("Plugin load failed: %s", plugin_id); return False
    def unload(self, plugin_id):
        plugin=self.plugins.pop(plugin_id, None)
        if plugin: plugin.shutdown()
        self.registry.unregister_plugin(plugin_id)
    def reload(self, plugin_id): self.unload(plugin_id); return self.load(plugin_id)
    def set_enabled(self, plugin_id, enabled):
        self.manifests[plugin_id]["enabled"]=enabled
        (self.directory/plugin_id/"manifest.json").write_text(json.dumps(self.manifests[plugin_id],ensure_ascii=False,indent=2),encoding="utf-8")
        return self.reload(plugin_id) if enabled else not bool(self.unload(plugin_id))
    def execute(self, plugin_id, command): return self.plugins[plugin_id].execute(command)
