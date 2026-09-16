class AssistantPlugin:
    """Base class for isolated capability plugins. Never execute raw shell text."""
    def initialize(self, context): self.context = context
    def shutdown(self): pass
    def get_commands(self): return []
    def execute(self, command): raise NotImplementedError
