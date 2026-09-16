import tkinter as tk
from tkinter import ttk, messagebox

class PluginManagerWindow(tk.Toplevel):
    def __init__(self, parent, assistant):
        super().__init__(parent); self.assistant=assistant; self.title("JARVIS — Plugins"); self.geometry("760x460")
        self.tree=ttk.Treeview(self,columns=("version","status","permissions","commands"),show="headings")
        for column,title,width in (("version","Version",80),("status","Status",90),("permissions","Permissions",180),("commands","Commands",350)):
            self.tree.heading(column,text=title); self.tree.column(column,width=width,stretch=True)
        self.tree.pack(fill="both",expand=True,padx=12,pady=12)
        controls=ttk.Frame(self); controls.pack(fill="x",padx=12,pady=(0,12))
        ttk.Button(controls,text="Включить",command=lambda:self.change(True)).pack(side="left",padx=3)
        ttk.Button(controls,text="Выключить",command=lambda:self.change(False)).pack(side="left",padx=3)
        ttk.Button(controls,text="Перезагрузить",command=self.reload).pack(side="left",padx=3)
        ttk.Button(controls,text="Команды",command=self.show_commands).pack(side="left",padx=3)
        self.refresh()
    def selected(self):
        chosen=self.tree.selection(); return chosen[0] if chosen else None
    def refresh(self):
        self.assistant.plugins.load_all()
        self.tree.delete(*self.tree.get_children())
        for folder in self.assistant.plugins.discover():
            plugin_id=folder.name; manifest=self.assistant.plugins.manifests.get(plugin_id,{})
            enabled=plugin_id in self.assistant.plugins.plugins
            status="Enabled" if enabled else ("Error" if plugin_id in self.assistant.plugins.errors else "Disabled")
            commands=", ".join(k for k,v in self.assistant.registry.commands().items() if v==plugin_id)
            self.tree.insert("","end",iid=plugin_id,values=(manifest.get("version","—"),status,", ".join(manifest.get("permissions",[])),commands),text=manifest.get("name",plugin_id))
    def change(self, enabled):
        plugin_id=self.selected()
        if plugin_id: self.assistant.plugins.set_enabled(plugin_id,enabled); self.refresh()
    def reload(self):
        plugin_id=self.selected()
        if plugin_id: self.assistant.plugins.reload(plugin_id); self.refresh()
    def show_commands(self):
        plugin_id=self.selected()
        if plugin_id: messagebox.showinfo("Commands", "\n".join(k for k,v in self.assistant.registry.commands().items() if v==plugin_id) or "Нет команд")
