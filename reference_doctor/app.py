"""Reference Doctor desktop program."""
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import appkit as ak
from . import bib
from .appkit import Entity, Field, entities

SCHEMA = entities(
    Entity("library", "Library", "Saved libraries", "name", [
        Field("name", "Name", required=True, list_col=True, width=30),
        Field("entries", "Entries", "int", list_col=True, width=8),
        Field("day", "Saved", "date", list_col=True, width=11),
        Field("data", "Data", "long"),
    ], "id DESC"),
)


def sample(store):
    store.set_setting("current", json.dumps(bib.parse_bibtex(bib.SAMPLE)[0]))


class Model:
    """The bibliography being worked on, shared by all screens."""

    def __init__(self, app):
        self.app = app
        self.entries = json.loads(app.store.get_setting("current", "[]"))

    def set(self, entries):
        self.entries = entries
        self.app.store.set_setting("current", json.dumps(entries))


def model(app):
    if not hasattr(app, "model"):
        app.model = Model(app)
    return app.model


class Doctor(ttk.Frame):
    title = "Diagnosis"

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open file", style="Accent.TButton", command=self.open_file).pack(side="left")
        ttk.Button(top, text="Paste", command=self.paste).pack(side="left", padx=6)
        ttk.Button(top, text="Apply all safe repairs", command=self.repair).pack(side="left")
        ttk.Button(top, text="Merge duplicates", command=self.dedupe).pack(side="left", padx=6)
        self.tiles = ttk.Frame(self)
        self.tiles.pack(fill="x", pady=8)
        self.table = ak.Table(self, app, [("level", "Level", 8), ("key", "Entry", 22), ("field", "Field", 10), ("msg", "Problem", 56), ("fix", "Repair", 8)])
        self.table.pack(fill="both", expand=True)
        self.note = ttk.Label(self, text="", style="Muted.TLabel")
        self.note.pack(anchor="w", pady=4)

    def open_file(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Bibliographies", "*.bib *.ris *.json *.txt"), ("All files", "*.*")])
        if path:
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.load(fh.read())

    def load(self, text):
        try:
            entries, problems, fmt = bib.parse_any(text)
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("Read file", str(exc), parent=self)
            return False
        model(self.app).set(entries)
        self.note.configure(text="Read %d entries as %s. %s" % (len(entries), fmt, " ".join(problems)))
        self.refresh()
        return True

    def paste(self):
        win = tk.Toplevel(self)
        win.title("Paste a bibliography")
        win.geometry("%dx%d" % (640 * self.app.scale, 400 * self.app.scale))
        win.transient(self.winfo_toplevel())
        win.configure(bg=self.app.colors["bg"])
        frame, t = ak.make_text(win, self.app.colors, mono=True)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        def go():
            if self.load(ak.get_text(t)):
                win.destroy()
        ttk.Button(win, text="Read", style="Accent.TButton", command=go).pack(pady=(0, 8))

    def repair(self):
        m = model(self.app)
        fixed, changes = bib.apply_fixes(m.entries)
        m.set(fixed)
        self.note.configure(text="%d repairs applied." % len(changes))
        self.refresh()

    def dedupe(self):
        m = model(self.app)
        merged, n = bib.merge_all(m.entries)
        m.set(merged)
        self.note.configure(text="%d duplicate entries merged." % n)
        self.refresh()

    def refresh(self):
        m = model(self.app)
        issues = bib.diagnose(m.entries)
        s = bib.summary(m.entries)
        count = lambda lvl: sum(1 for i in issues if i.level == lvl)
        for w in self.tiles.winfo_children():
            w.destroy()
        ak.tiles(self.tiles, [("Entries", str(s["entries"]), "%s to %s" % (s["oldest"] or "-", s["newest"] or "-")), ("Errors", str(count("error")), "must fix"),
                              ("Warnings", str(count("warning")), ""), ("Possible duplicates", str(sum(len(g) - 1 for g in bib.duplicates(m.entries))), ""),
                              ("With a DOI", str(s["with_doi"]), "of %d" % s["entries"])]).pack(fill="x")
        tags = {"error": "danger", "warning": "warning", "info": "muted"}
        self.table.fill([(k, (i.level, i.key, i.field, i.message, "Yes" if i.fix else ""), tags[i.level]) for k, i in enumerate(issues)])


class Entries(ttk.Frame):
    title = "Entries"

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        pane.bind("<Map>", lambda e: pane.after_idle(lambda: pane.sashpos(0, int(pane.winfo_width() * 0.42))))
        self.table = ak.Table(left, app, [("key", "Key", 18), ("year", "Year", 6), ("title", "Title", 40)], on_select=self.show)
        self.table.pack(fill="both", expand=True)
        ttk.Label(right, text="Edit the entry as BibTeX", style="Muted.TLabel").pack(anchor="w")
        frame, self.text = ak.make_text(right, app.colors, height=16, mono=True)
        frame.pack(fill="both", expand=True)
        row = ttk.Frame(right)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Save entry", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(row, text="Delete entry", command=self.delete).pack(side="left", padx=6)
        self.sel = None

    def refresh(self):
        es = model(self.app).entries
        self.table.fill([(i, (e["key"], e.get("year", ""), ak.short(e.get("title", ""), 70))) for i, e in enumerate(es)])
        self.sel = None
        ak.set_text(self.text, "")
        if es:
            self.table.tree.selection_set("0")
            self.show("0")

    def show(self, iid):
        if iid is not None:
            self.sel = int(iid)
            ak.set_text(self.text, bib.to_bibtex([model(self.app).entries[self.sel]]))

    def save(self):
        if self.sel is None:
            return
        try:
            parsed, problems = bib.parse_bibtex(ak.get_text(self.text))
        except ValueError as exc:
            messagebox.showerror("Entry", str(exc), parent=self)
            return
        if len(parsed) != 1 or problems:
            messagebox.showerror("Entry", "Keep exactly one complete entry here.\n" + " ".join(problems), parent=self)
            return
        m = model(self.app)
        es = list(m.entries)
        es[self.sel] = parsed[0]
        keep = self.sel
        m.set(es)
        self.refresh()
        self.table.tree.selection_set(str(keep))
        self.app.status("Entry saved")

    def delete(self):
        if self.sel is not None and messagebox.askyesno("Delete", "Delete this entry?", parent=self):
            m = model(self.app)
            m.set([e for i, e in enumerate(m.entries) if i != self.sel])
            self.refresh()


class Convert(ttk.Frame):
    title = "Convert and format"

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.fmt = tk.StringVar(value="APA")
        ak.labeled_combo(top, "Output", self.fmt, bib.STYLES + ["BibTeX", "RIS", "CSL JSON"], 14, self.show).pack(side="left")
        ttk.Button(top, text="Copy", command=self.copy).pack(side="left", padx=6, anchor="s")
        ttk.Button(top, text="Save as a file", style="Accent.TButton", command=self.save).pack(side="left", anchor="s")
        frame, self.text = ak.make_text(self, app.colors, height=20, mono=True)
        frame.pack(fill="both", expand=True, pady=6)

    def show(self):
        es = model(self.app).entries
        f = self.fmt.get()
        if f == "BibTeX":
            t = bib.to_bibtex(es)
        elif f == "RIS":
            t = bib.to_ris(es)
        elif f == "CSL JSON":
            t = bib.to_csl(es)
        else:
            t = bib.reference_list(es, f)
        ak.set_text(self.text, t)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(ak.get_text(self.text))
        self.app.status("Copied")

    def save(self):
        ext = {"BibTeX": ".bib", "RIS": ".ris", "CSL JSON": ".json"}.get(self.fmt.get(), ".txt")
        path = filedialog.asksaveasfilename(parent=self, defaultextension=ext, initialfile="references" + ext)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(ak.get_text(self.text))
            self.app.status("Saved " + path)

    def refresh(self):
        self.show()


class Libraries(ttk.Frame):
    title = "Saved libraries"

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.view = ak.EntityView(self, app, "library", skip=("data", "entries", "day"), buttons=[("Save current", self.keep), ("Load", self.load)])
        self.view.pack(fill="both", expand=True)
        self.refresh, self.commit = self.view.refresh, self.view.commit

    def keep(self, name=None):
        from tkinter import simpledialog
        name = name or simpledialog.askstring("Save", "Name:", parent=self)
        if name:
            es = model(self.app).entries
            self.app.store.insert("library", name=name, entries=len(es), day=ak.today(), data=json.dumps(es))
            self.refresh()

    def load(self):
        if self.view.current:
            model(self.app).set(json.loads(self.app.store.get("library", self.view.current)["data"]))
            self.app.navigate("Diagnosis")


class APP(ak.BaseApp):
    APP_NAME, ACCENT = "Reference Doctor", "#c026d3"
    SCHEMA, SAMPLE = SCHEMA, staticmethod(sample)
    VIEWS = [Doctor, Entries, Convert, Libraries, ak.DataView]


def main(argv=None):
    return ak.main(APP, argv)
