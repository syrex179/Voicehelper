import os
import sys
from pathlib import Path

class WindowsAutostart:
    KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE="JARVISAssistant"
    def command(self):
        main=Path(__file__).parents[1]/"main.py"
        return '"{}" "{}"'.format(sys.executable,main)
    def available(self):
        try: import winreg; return True
        except ImportError: return False
    def enabled(self):
        if not self.available(): return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.KEY,0,winreg.KEY_READ) as key: return bool(winreg.QueryValueEx(key,self.VALUE)[0])
        except FileNotFoundError: return False
    def set_enabled(self, enabled):
        if not self.available(): raise RuntimeError("Autostart доступен только в Windows.")
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,self.KEY) as key:
            if enabled: winreg.SetValueEx(key,self.VALUE,0,winreg.REG_SZ,self.command())
            else:
                try: winreg.DeleteValue(key,self.VALUE)
                except FileNotFoundError: pass
        return self.enabled()==enabled
