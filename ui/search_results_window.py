import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path

class SearchResultsWindow(tk.Toplevel):
    def __init__(self,parent,results):
        super().__init__(parent); self.results=results; self.title("JARVIS — File search"); self.geometry("900x420")
        self.tree=ttk.Treeview(self,columns=("path","type","size","modified"),show="headings")
        for key,title,width in (("path","Path",460),("type","Type",80),("size","Bytes",90),("modified","Modified",160)):
            self.tree.heading(key,text=title); self.tree.column(key,width=width,stretch=True)
        self.tree.pack(fill="both",expand=True,padx=12,pady=12)
        for index,item in enumerate(results): self.tree.insert("","end",iid=str(index),values=(item["path"],item["type"],item["size"],item["modified"]))
        bar=ttk.Frame(self); bar.pack(pady=(0,12)); ttk.Button(bar,text="Открыть",command=self.open).pack(side="left",padx=4); ttk.Button(bar,text="Показать в проводнике",command=self.folder).pack(side="left",padx=4)
    def selected(self):
        selected=self.tree.selection(); return self.results[int(selected[0])] if selected else None
    def open(self):
        item=self.selected()
        if item: os.startfile(item["path"])
    def folder(self):
        item=self.selected()
        if item: os.startfile(str(Path(item["path"]).parent))
