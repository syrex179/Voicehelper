"""Optional real Windows notification-area icon using pystray."""
import threading

class Tray:
    def __init__(self, window): self.window=window; self.icon=None
    def start(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            image=Image.new("RGBA",(64,64),(16,22,34,255)); draw=ImageDraw.Draw(image); draw.ellipse((12,12,52,52),outline=(88,214,255,255),width=4)
            menu=pystray.Menu(
                pystray.MenuItem("Open JARVIS",lambda: self.window.root.after(0,self.window.show)),
                pystray.MenuItem("Enable microphone",lambda: self.window.root.after(0,self.window.enable_microphone)),
                pystray.MenuItem("Disable microphone",lambda: self.window.root.after(0,self.window.disable_microphone)),
                pystray.MenuItem("My skills",lambda: self.window.root.after(0,self.window.open_skills)),
                pystray.MenuItem("Plugins",lambda: self.window.root.after(0,self.window.open_plugins)),
                pystray.MenuItem("Settings",lambda: self.window.root.after(0,self.window.open_settings)),
                pystray.MenuItem("Exit",lambda: self.window.root.after(0,self.window.exit)),
            )
            self.icon=pystray.Icon("JARVIS",image,"JARVIS",menu); threading.Thread(target=self.icon.run,daemon=True).start(); return True
        except ImportError: return False
    def stop(self):
        if self.icon: self.icon.stop()
