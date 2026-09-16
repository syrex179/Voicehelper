import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

class SkillManagerWindow(tk.Toplevel):
    def __init__(self,parent,assistant,on_change):
        super().__init__(parent); self.assistant,self.on_change=assistant,on_change; self.title("JARVIS — Skills"); self.geometry("820x480")
        self.tree=ttk.Treeview(self,columns=("trigger","version","updated","actions"),show="tree headings")
        self.tree.heading("#0",text="Skill"); self.tree.heading("trigger",text="Trigger"); self.tree.heading("version",text="Version"); self.tree.heading("updated",text="Updated"); self.tree.heading("actions",text="Actions")
        self.tree.pack(fill="both",expand=True,padx=12,pady=12)
        bar=ttk.Frame(self); bar.pack(fill="x",padx=12,pady=(0,12))
        for label,callback in (("Run",self.run),("Rename",self.rename),("Icon",self.icon),("Duplicate",self.duplicate),("Export",self.export),("Import",self.import_skill),("Delete",self.delete)):
            ttk.Button(bar,text=label,command=callback).pack(side="left",padx=2)
        self.refresh()
    def selected(self):
        chosen=self.tree.selection(); return chosen[0] if chosen else None
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for s in self.assistant.memory.skills:
            actions=", ".join(a["intent"].replace("_"," ") for a in s["actions"])
            self.tree.insert("","end",iid=s["id"],text=f"{s.get('icon','⚡')} {s['name']}",values=(s.get("trigger",""),s.get("version",1),s.get("updated_at","—"),actions))
    def skill(self): return next((s for s in self.assistant.memory.skills if s["id"]==self.selected()),None)
    def changed(self): self.refresh(); self.on_change()
    def run(self):
        s=self.skill()
        if s: messagebox.showinfo("JARVIS",self.assistant.skills.run(s["name"]).message); self.changed()
    def rename(self):
        s=self.skill()
        if s:
            name=simpledialog.askstring("Rename","New name",initialvalue=s["name"],parent=self)
            if name: self.assistant.skills.update(s["name"],name=name,trigger=name.lower()); self.changed()
    def icon(self):
        s=self.skill()
        if s:
            value=simpledialog.askstring("Icon","Emoji",initialvalue=s.get("icon","⚡"),parent=self)
            if value: self.assistant.skills.update(s["name"],icon=value); self.changed()
    def duplicate(self):
        s=self.skill()
        if s: self.assistant.skills.duplicate(s["name"]); self.changed()
    def export(self):
        s=self.skill()
        if s:
            path=filedialog.asksaveasfilename(defaultextension=".json",initialfile=s["name"]+".json",filetypes=[("Skill JSON","*.json")])
            if path: self.assistant.skills.export_skill(s["name"],path)
    def import_skill(self):
        path=filedialog.askopenfilename(filetypes=[("Skill JSON","*.json")])
        if path:
            try: self.assistant.skills.import_skill(path); self.changed()
            except (OSError,ValueError) as exc: messagebox.showerror("Import",str(exc))
    def delete(self):
        s=self.skill()
        if s and messagebox.askyesno("Delete",f"Delete {s['name']}?"):
            self.assistant.skills.delete(s["name"]); self.changed()
