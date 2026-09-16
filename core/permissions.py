from .models import Result

class PermissionManager:
    DANGEROUS = {"SHUTDOWN", "RESTART", "LOCK", "DELETE_FILE"}
    def check(self, command, confirmed=False):
        if command.intent in self.DANGEROUS and not confirmed:
            return Result(False, "Это действие требует подтверждения.", confirmation_required=True)
        return None
