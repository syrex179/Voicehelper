from collections import defaultdict

class EventBus:
    def __init__(self): self._handlers = defaultdict(list)
    def subscribe(self, event, handler): self._handlers[event].append(handler)
    def emit(self, event, **payload):
        for handler in list(self._handlers[event]):
            try: handler(**payload)
            except Exception: pass
