"""Shared desktop framework: data model, SQLite store, theme, widgets, main window and command line.

This file is vendored unchanged into each application of the suite, so every program builds on its own."""
import argparse
import datetime
import json
import os
import queue
import sqlite3
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

FONT, MONO = "Segoe UI", "Consolas"
TYPES = {"text": "TEXT", "long": "TEXT", "date": "TEXT", "choice": "TEXT", "number": "REAL", "int": "INTEGER",
         "bool": "INTEGER", "ref": "INTEGER"}


class ValidationError(Exception):
    pass


def today():
    return datetime.date.today().isoformat()


def parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def short(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


# ---------------- data model and storage ----------------
class Field:
    def __init__(self, name, label, kind="text", choices=None, required=False, ref=None, width=None, list_col=False,
                 default=None):
        self.name, self.label, self.kind, self.choices = name, label, kind, choices
        self.required, self.ref, self.width, self.list_col, self.default = required, ref, width, list_col, default


class Entity:
    def __init__(self, name, label, plural, title_field, fields, order=None):
        self.name, self.label, self.plural, self.title_field = name, label, plural, title_field
        self.fields, self.order = fields, order or "id"

    def field(self, name):
        return next(f for f in self.fields if f.name == name)

    def list_fields(self):
        return [f for f in self.fields if f.list_col]


def entities(*items):
    return {e.name: e for e in items}


class Store:
    """Generic validated storage for a dict of entities."""

    def __init__(self, schema, path=":memory:", app_name="app"):
        self.schema, self.path, self.app_name = schema, path, app_name
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        for e in schema.values():
            cols = ["id INTEGER PRIMARY KEY"]
            for f in e.fields:
                col = "%s %s" % (f.name, TYPES[f.kind])
                if f.kind == "ref":
                    col += ' REFERENCES "%s"(id) ON DELETE %s' % (f.ref, "CASCADE" if f.required else "SET NULL")
                cols.append(col)
            self.conn.execute('CREATE TABLE IF NOT EXISTS "%s" (%s)' % (e.name, ", ".join(cols)))
            have = {r["name"] for r in self.conn.execute('PRAGMA table_info("%s")' % e.name)}
            for f in e.fields:
                if f.name not in have:
                    self.conn.execute('ALTER TABLE "%s" ADD COLUMN %s %s' % (e.name, f.name, TYPES[f.kind]))
        self.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    def get_setting(self, key, default=""):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r and r["value"] is not None else default

    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, str(value)))
        self.conn.commit()

    def clean(self, entity, data, partial=False):
        out = {}
        for f in self.schema[entity].fields:
            if f.name not in data:
                if partial:
                    continue
                value = f.default
            else:
                value = data[f.name]
            if f.kind == "bool" and value in (None, ""):
                value = 0
            if isinstance(value, str) and f.kind != "long":
                value = value.strip()
            if value == "":
                value = None
            if value is None:
                if f.required:
                    raise ValidationError("%s is required" % f.label)
                out[f.name] = None
                continue
            try:
                if f.kind == "date":
                    d = parse_date(str(value))
                    if d is None:
                        raise ValidationError("%s must be a date as YYYY-MM-DD" % f.label)
                    value = d.isoformat()
                elif f.kind == "number":
                    value = float(value)
                elif f.kind in ("int", "ref"):
                    value = int(float(value))
                elif f.kind == "bool":
                    value = 1 if value in (1, True, "1", "true", "True", "yes") else 0
                elif f.kind == "choice" and value not in f.choices:
                    raise ValidationError("%s must be one of: %s" % (f.label, ", ".join(f.choices)))
            except (TypeError, ValueError):
                raise ValidationError("%s has an invalid value: %r" % (f.label, value))
            out[f.name] = value
        return out

    def insert(self, entity, **data):
        row = self.clean(entity, data)
        keys = list(row)
        cur = self.conn.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (entity, ", ".join(keys), ", ".join("?" * len(keys))),
                                [row[k] for k in keys])
        self.conn.commit()
        return cur.lastrowid

    def update(self, entity, rid, **data):
        row = self.clean(entity, data, partial=True)
        if row:
            self.conn.execute('UPDATE "%s" SET %s WHERE id=?' % (entity, ", ".join("%s=?" % k for k in row)),
                              list(row.values()) + [rid])
            self.conn.commit()

    def delete(self, entity, rid):
        self.conn.execute('DELETE FROM "%s" WHERE id=?' % entity, (rid,))
        self.conn.commit()

    def get(self, entity, rid):
        r = self.conn.execute('SELECT * FROM "%s" WHERE id=?' % entity, (rid,)).fetchone()
        return dict(r) if r else None

    def list(self, entity, where=None, params=(), order=None):
        sql = 'SELECT * FROM "%s"' % entity
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY " + (order or self.schema[entity].order)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def count(self, entity, where=None, params=()):
        sql = 'SELECT COUNT(*) FROM "%s"' % entity + (" WHERE " + where if where else "")
        return self.conn.execute(sql, params).fetchone()[0]

    def search(self, entity, text):
        cols = [f.name for f in self.schema[entity].fields if f.kind in ("text", "long", "choice")]
        words = text.lower().split()
        return [r for r in self.list(entity)
                if all(any(w in str(r.get(c) or "").lower() for c in cols) for w in words)]

    def label(self, entity, rid):
        r = self.get(entity, rid) if rid else None
        return "" if not r else str(r.get(self.schema[entity].title_field) or "#%d" % rid)

    def choices(self, entity):
        return [(r["id"], self.label(entity, r["id"])) for r in self.list(entity)]

    def _order(self):
        """Parents before children, so foreign keys hold while restoring."""
        done, out = set(), []

        def visit(name):
            if name in done:
                return
            done.add(name)
            for f in self.schema[name].fields:
                if f.kind == "ref" and f.ref != name:
                    visit(f.ref)
            out.append(name)
        for name in self.schema:
            visit(name)
        return out

    def dump(self):
        data = {"app": self.app_name, "settings": {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM settings")}}
        for name in self.schema:
            data[name] = [dict(r) for r in self.conn.execute('SELECT * FROM "%s"' % name)]
        return data

    def backup(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.dump(), fh, indent=1, ensure_ascii=False)
        return path

    def restore(self, path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("app") != self.app_name:
            raise ValidationError("this is not a backup of %s" % self.app_name)
        order = self._order()
        with self.conn:
            self.conn.execute("PRAGMA defer_foreign_keys = ON")
            for name in reversed(order):
                self.conn.execute('DELETE FROM "%s"' % name)
            self.conn.execute("DELETE FROM settings")
            for name in order:
                allowed = {"id"} | {f.name for f in self.schema[name].fields}
                for row in data.get(name, []):
                    keys = [k for k in row if k in allowed]
                    self.conn.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (name, ", ".join(keys), ", ".join("?" * len(keys))),
                                      [row[k] for k in keys])
            for k, v in data.get("settings", {}).items():
                self.conn.execute("INSERT INTO settings VALUES (?, ?)", (k, v))


def data_path(app_name):
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "".join(c for c in app_name if c.isalnum()))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "data.sqlite3")


# ---------------- theme ----------------
PALETTES = {
    "Light": dict(bg="#f4f5f7", panel="#ffffff", ink="#1d2433", muted="#667085", line="#dfe3e8", accent="#2f5fd0",
                  accent_ink="#ffffff", side="#172033", side_ink="#c9d1e0", sel="#dbe6ff", sel_ink="#1d2433",
                  danger="#b42318", warning="#b54708", ok="#067647", info="#475467", track="#e8ebf0"),
    "Dark": dict(bg="#14171d", panel="#1c2029", ink="#e6e9ef", muted="#98a2b3", line="#2f3542", accent="#6d94f2",
                 accent_ink="#10131a", side="#0e1116", side_ink="#aeb7c6", sel="#2c3a5e", sel_ink="#e6e9ef",
                 danger="#f97066", warning="#fdb022", ok="#47cd89", info="#98a2b3", track="#2a2f3a"),
}


def ui_scale(root):
    """Display scaling: 1.0 at 96 dpi, 1.5 on a 150% display."""
    return max(1.0, root.winfo_fpixels("1i") / 96.0)


def apply_theme(root, name, accent=None):
    c = dict(PALETTES[name])
    if accent and name == "Light":
        c["accent"] = accent
    scale = ui_scale(root)
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=c["bg"])
    s.configure(".", background=c["bg"], foreground=c["ink"], fieldbackground=c["panel"], bordercolor=c["line"],
                lightcolor=c["line"], darkcolor=c["line"], font=(FONT, 10), troughcolor=c["track"],
                selectbackground=c["sel"], selectforeground=c["sel_ink"], insertcolor=c["ink"])
    s.configure("TFrame", background=c["bg"])
    s.configure("Panel.TFrame", background=c["panel"])
    s.configure("TLabel", background=c["bg"], foreground=c["ink"])
    s.configure("Panel.TLabel", background=c["panel"], foreground=c["ink"])
    s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
    s.configure("PanelMuted.TLabel", background=c["panel"], foreground=c["muted"], font=(FONT, 9))
    s.configure("Title.TLabel", background=c["bg"], foreground=c["ink"], font=(FONT, 17, "bold"))
    s.configure("Head.TLabel", background=c["panel"], foreground=c["ink"], font=(FONT, 11, "bold"))
    s.configure("Kpi.TLabel", background=c["panel"], foreground=c["ink"], font=(FONT, 20, "bold"))
    for n, col in (("Danger", "danger"), ("Warning", "warning"), ("Info", "info"), ("Ok", "ok")):
        s.configure(n + ".TLabel", background=c["panel"], foreground=c[col])
    s.configure("TButton", background=c["track"], foreground=c["ink"], padding=(10, 5), bordercolor=c["line"],
                focuscolor=c["track"], relief="flat", width=-6)
    s.map("TButton", background=[("active", c["sel"]), ("disabled", c["bg"])], foreground=[("disabled", c["muted"])])
    s.configure("Accent.TButton", background=c["accent"], foreground=c["accent_ink"], bordercolor=c["accent"],
                font=(FONT, 10, "bold"))
    s.map("Accent.TButton", background=[("active", c["accent"]), ("disabled", c["line"])])
    s.configure("Nav.TButton", background=c["side"], foreground=c["side_ink"], anchor="w", padding=(16, 8),
                bordercolor=c["side"], relief="flat", focuscolor=c["side"])
    s.map("Nav.TButton", background=[("active", c["sel"])], foreground=[("active", c["sel_ink"])])
    s.configure("NavOn.TButton", background=c["accent"], foreground=c["accent_ink"], anchor="w", padding=(16, 8),
                bordercolor=c["accent"], relief="flat", font=(FONT, 10, "bold"), focuscolor=c["accent"])
    s.map("NavOn.TButton", background=[("active", c["accent"])])
    s.configure("Side.TFrame", background=c["side"])
    s.configure("Side.TLabel", background=c["side"], foreground="#ffffff", font=(FONT, 12, "bold"))
    s.configure("SideMuted.TLabel", background=c["side"], foreground=c["side_ink"], font=(FONT, 8))
    s.configure("TEntry", fieldbackground=c["panel"], foreground=c["ink"], insertcolor=c["ink"], padding=4)
    s.configure("TCombobox", fieldbackground=c["panel"], foreground=c["ink"], background=c["panel"],
                arrowcolor=c["ink"], padding=3)
    s.map("TCombobox", fieldbackground=[("readonly", c["panel"])], foreground=[("readonly", c["ink"])],
          selectbackground=[("readonly", c["panel"])], selectforeground=[("readonly", c["ink"])])
    s.configure("TCheckbutton", background=c["panel"], foreground=c["ink"])
    s.map("TCheckbutton", background=[("active", c["panel"])])
    s.configure("TRadiobutton", background=c["panel"], foreground=c["ink"])
    s.map("TRadiobutton", background=[("active", c["panel"])])
    s.configure("TNotebook", background=c["bg"], bordercolor=c["line"], tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"], padding=(12, 6), bordercolor=c["line"])
    s.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["ink"])])
    s.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["ink"],
                rowheight=int(26 * scale), bordercolor=c["line"], font=(FONT, 10))
    s.map("Treeview", background=[("selected", c["sel"])], foreground=[("selected", c["sel_ink"])])
    s.configure("Treeview.Heading", background=c["bg"], foreground=c["muted"], relief="flat", padding=(6, 5),
                font=(FONT, 9, "bold"))
    s.map("Treeview.Heading", background=[("active", c["line"])])
    s.configure("TPanedwindow", background=c["bg"])
    s.configure("Horizontal.TScale", background=c["bg"], troughcolor=c["track"])
    s.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["track"])
    root.option_add("*TCombobox*Listbox.background", c["panel"])
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.foreground", c["ink"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["sel"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["sel_ink"])
    return c


# ---------------- widgets ----------------
def make_text(parent, colors, height=6, wrap="word", mono=False, **kw):
    """A themed Text with a scrollbar inside its own frame. Returns (frame, text)."""
    frame = ttk.Frame(parent)
    t = tk.Text(frame, height=height, wrap=wrap, undo=True, relief="flat", bd=0, padx=8, pady=6, bg=colors["panel"],
                fg=colors["ink"], insertbackground=colors["ink"], selectbackground=colors["sel"],
                selectforeground=colors["sel_ink"], highlightthickness=1, highlightbackground=colors["line"],
                highlightcolor=colors["accent"], font=(MONO if mono else FONT, 10), **kw)
    sb = ttk.Scrollbar(frame, command=t.yview)
    t.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    t.pack(side="left", fill="both", expand=True)
    return frame, t


def set_text(widget, text, readonly=False):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    if readonly:
        widget.configure(state="disabled")


def get_text(widget):
    return widget.get("1.0", "end-1c")


class ScrollFrame(ttk.Frame):
    """A vertically scrolling container: add children to `.body`."""

    def __init__(self, parent, style="TFrame"):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=ttk.Style().lookup(style, "background"))
        self.bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style=style)
        self.win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


class Table(ttk.Frame):
    """A Treeview with scrollbars. columns: [(id, heading, width)] with width in characters."""

    def __init__(self, parent, app, columns, height=10, on_select=None, stretch_last=True):
        super().__init__(parent)
        self.app, self.columns = app, columns
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", height=height, selectmode="browse")
        for i, (cid, head, width) in enumerate(columns):
            self.tree.heading(cid, text=head)
            self.tree.column(cid, width=int(width * 7 * app.scale), minwidth=int(40 * app.scale), anchor="w",
                             stretch=stretch_last and i == len(columns) - 1)
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        hb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
        hb.pack(side="bottom", fill="x")
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        for tag in ("danger", "warning", "ok"):
            self.tree.tag_configure(tag, foreground=app.colors[tag])
        self.tree.tag_configure("muted", foreground=app.colors["muted"])
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", lambda e: on_select(self.selected()))

    def fill(self, rows):
        """rows: list of value lists, or (iid, values[, tag]) tuples."""
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            if isinstance(r, tuple) and len(r) >= 2 and isinstance(r[1], (list, tuple)):
                iid, values = r[0], r[1]
                tag = (r[2],) if len(r) > 2 and r[2] else ()
                self.tree.insert("", "end", iid=str(iid), values=list(values), tags=tag)
            else:
                self.tree.insert("", "end", values=list(r))

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None


class Form(ttk.Frame):
    """Inputs for one entity, generated from the schema."""

    def __init__(self, parent, app, entity, skip=()):
        super().__init__(parent, style="Panel.TFrame")
        self.app, self.entity = app, app.store.schema[entity]
        self.scroll = ScrollFrame(self, "Panel.TFrame")
        self.scroll.pack(fill="both", expand=True)
        self.inputs, self.ref_maps, self.baseline = {}, {}, {}
        body = self.scroll.body
        body.columnconfigure(0, weight=1)
        row = 0
        for f in self.entity.fields:
            if f.name in skip:
                continue
            if f.kind == "bool":
                var = tk.IntVar()
                ttk.Checkbutton(body, text=f.label, variable=var).grid(row=row, column=0, sticky="w", padx=12, pady=(8, 0))
                self.inputs[f.name] = var
                row += 1
                continue
            ttk.Label(body, text=f.label + (" *" if f.required else ""), style="PanelMuted.TLabel").grid(
                row=row, column=0, sticky="w", padx=12, pady=(8, 0))
            row += 1
            if f.kind == "long":
                frame, t = make_text(body, app.colors, height=5)
                frame.grid(row=row, column=0, sticky="ew", padx=12)
                self.inputs[f.name] = t
            elif f.kind in ("choice", "ref"):
                var = tk.StringVar()
                box = ttk.Combobox(body, textvariable=var, state="readonly", values=f.choices or [])
                box.grid(row=row, column=0, sticky="ew", padx=12)
                self.inputs[f.name] = var
                if f.kind == "ref":
                    self.ref_maps[f.name] = (box, {})
            else:
                var = tk.StringVar()
                ttk.Entry(body, textvariable=var).grid(row=row, column=0, sticky="ew", padx=12)
                self.inputs[f.name] = var
            row += 1
        ttk.Frame(body, height=10, style="Panel.TFrame").grid(row=row, column=0)

    def refresh_refs(self):
        for name, (box, _) in self.ref_maps.items():
            f = self.entity.field(name)
            seen, labels = {}, [""]
            for rid, label in self.app.store.choices(f.ref):
                key = label if label not in seen else "%s (#%d)" % (label, rid)
                seen[key] = rid
                labels.append(key)
            box.configure(values=labels)
            self.ref_maps[name] = (box, seen)

    def _set(self, name, value):
        w = self.inputs[name]
        if isinstance(w, tk.Text):
            set_text(w, value or "")
            w.edit_reset()
        elif name in self.ref_maps:
            w.set(next((k for k, v in self.ref_maps[name][1].items() if v == value), ""))
        elif isinstance(w, tk.IntVar):
            w.set(1 if value else 0)
        else:
            w.set("" if value is None else ("%g" % value if isinstance(value, float) else value))

    def load(self, row):
        self.refresh_refs()
        for f in self.entity.fields:
            if f.name in self.inputs:
                self._set(f.name, row.get(f.name, f.default) if row else f.default)
        self.baseline = self.read()

    def read(self):
        out = {}
        for name, w in self.inputs.items():
            if isinstance(w, tk.Text):
                out[name] = get_text(w)
            elif name in self.ref_maps:
                out[name] = self.ref_maps[name][1].get(w.get())
            else:
                out[name] = w.get()
        return out

    def dirty(self):
        return self.read() != self.baseline


class EntityView(ttk.Frame):
    """Searchable list on the left and a form on the right, for any entity."""

    def __init__(self, parent, app, entity, skip=(), buttons=(), tabs=None, on_select=None, split=0.62):
        super().__init__(parent)
        self.app, self.name, self.entity = app, entity, app.store.schema[entity]
        self.current, self.on_select = None, on_select
        self.rows, self._loading, self.opened = {}, False, False
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        pane.bind("<Map>", lambda e: pane.after_idle(lambda: pane.sashpos(0, int(pane.winfo_width() * split))))
        bar_ = ttk.Frame(left)
        bar_.pack(fill="x", pady=(0, 6))
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *a: self.refresh())
        ttk.Label(bar_, text="Search").pack(side="left", padx=(0, 6))
        ttk.Entry(bar_, textvariable=self.query, width=20).pack(side="left")
        ttk.Button(bar_, text="New", style="Accent.TButton", command=self.new).pack(side="left", padx=(10, 0))
        for text, cmd in buttons:
            ttk.Button(bar_, text=text, command=cmd).pack(side="left", padx=(6, 0))
        cols = self.entity.list_fields()
        self.table = Table(left, app, [(f.name, f.label, f.width or 14) for f in cols], stretch_last=False)
        self.table.pack(fill="both", expand=True)
        self.tree = self.table.tree
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        if tabs:
            nb = ttk.Notebook(right)
            nb.pack(fill="both", expand=True)
            form_parent = ttk.Frame(nb, style="Panel.TFrame")
            nb.add(form_parent, text="Details")
            tabs(nb, self)
        else:
            form_parent = right
        act = ttk.Frame(form_parent, style="Panel.TFrame", padding=8)
        act.pack(side="bottom", fill="x")
        ttk.Button(act, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(act, text="Delete", command=self.delete).pack(side="right")
        self.form = Form(form_parent, app, entity, skip)
        self.form.pack(fill="both", expand=True)
        self.form.load(None)

    def _cell(self, f, row):
        v = row.get(f.name)
        if f.kind == "ref":
            return self.app.store.label(f.ref, v)
        if f.kind == "bool":
            return "Yes" if v else ""
        if isinstance(v, float):
            return "%g" % v
        if f.kind == "long":
            return (v or "").replace("\n", " ")[:80]
        return "" if v is None else v

    def refresh(self):
        q = self.query.get().strip()
        rows = self.app.store.search(self.name, q) if q else self.app.store.list(self.name)
        keep = self.current
        self._loading = True
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for r in rows:
            self.rows[str(r["id"])] = r
            self.tree.insert("", "end", iid=str(r["id"]), values=[self._cell(f, r) for f in self.entity.list_fields()])
        if keep is not None and str(keep) in self.rows:
            self.tree.selection_set(str(keep))
        self._loading = False
        self.form.refresh_refs()
        if not self.opened:
            self.opened = True
            first = next(iter(self.rows), None)
            if first is not None and self.current is None:
                self._loading = True
                self.tree.selection_set(first)
                self._loading = False
                self._show(int(first))

    def select(self, rid):
        self.query.set("")
        self.refresh()
        if str(rid) in self.rows:
            self.tree.selection_set(str(rid))
            self.tree.see(str(rid))
            self._show(rid)

    def _selected(self, _event=None):
        if self._loading:
            return
        sel = self.tree.selection()
        if not sel or int(sel[0]) == self.current:
            return
        if not self.commit():
            self._loading = True
            if self.current is not None:
                self.tree.selection_set(str(self.current))
            self._loading = False
            return
        self._show(int(sel[0]))

    def _show(self, rid):
        self.current = rid
        row = self.app.store.get(self.name, rid)
        self.form.load(row)
        if self.on_select:
            self.on_select(row)

    def commit(self):
        return True if not self.form.dirty() else self.save(quiet=True)

    def new(self):
        if not self.commit():
            return
        self.current = None
        self.tree.selection_set(())
        self.form.load(None)
        if self.on_select:
            self.on_select(None)

    def save(self, quiet=False):
        data = self.form.read()
        try:
            if self.current is None:
                self.current = self.app.store.insert(self.name, **data)
            else:
                self.app.store.update(self.name, self.current, **data)
        except ValidationError as exc:
            messagebox.showerror("Please check this entry", str(exc), parent=self)
            return False
        self.form.baseline = self.form.read()
        self.refresh()
        if not quiet:
            self.tree.selection_set(str(self.current))
            self.app.status("%s saved" % self.entity.label)
            if self.on_select:
                self.on_select(self.app.store.get(self.name, self.current))
        return True

    def delete(self):
        if self.current is None:
            return
        label = self.app.store.label(self.name, self.current)
        if messagebox.askyesno("Delete", "Delete this %s?\n\n%s\n\nThis cannot be undone." % (self.entity.label.lower(), label),
                               parent=self):
            self.app.store.delete(self.name, self.current)
            self.current = None
            self.form.load(None)
            self.refresh()
            if self.on_select:
                self.on_select(None)
            self.app.status("Deleted")


class Worker:
    """Run a job on a thread. The job calls post(fn, *args) to run fn on the GUI thread, because Tk may only be
    touched from there. Results travel through a queue that the GUI polls."""

    def __init__(self, widget, job, *args):
        self.widget, self.q = widget, queue.Queue()
        self.thread = threading.Thread(target=self._go, args=(job, args), daemon=True)
        self.thread.start()
        self._pump()

    def _go(self, job, args):
        try:
            job(self.post, *args)
        finally:
            self.q.put(None)

    def post(self, fn, *args):
        self.q.put((fn, args))

    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    return
                item[0](*item[1])
        except queue.Empty:
            pass
        if self.widget.winfo_exists():
            self.widget.after(40, self._pump)


class Tile(ttk.Frame):
    def __init__(self, parent, label, value, note=""):
        super().__init__(parent, style="Panel.TFrame", padding=12)
        ttk.Label(self, text=label, style="PanelMuted.TLabel").pack(anchor="w")
        ttk.Label(self, text=value, style="Kpi.TLabel").pack(anchor="w")
        if note:
            ttk.Label(self, text=short(note, 30), style="PanelMuted.TLabel").pack(anchor="w")


def tiles(parent, items):
    """A row of KPI tiles: items = [(label, value, note)]."""
    row = ttk.Frame(parent)
    for i, (a, b, c) in enumerate(items):
        Tile(row, a, b, c).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
        row.columnconfigure(i, weight=1, uniform="t")
    return row


def bar(parent, fraction, colors, width=120, height=8, color=None):
    scale = ui_scale(parent)
    width, height = int(width * scale), int(height * scale)
    c = tk.Canvas(parent, width=width, height=height, bg=colors["panel"], highlightthickness=0, bd=0)
    c.create_rectangle(0, 0, width, height, fill=colors["track"], width=0)
    f = max(0.0, min(1.0, fraction))
    c.create_rectangle(0, 0, width * f, height, width=0,
                       fill=color or (colors["ok"] if f >= 0.7 else colors["warning"] if f >= 0.4 else colors["danger"]))
    return c


def panel(parent, title=None, padding=12):
    p = ttk.Frame(parent, style="Panel.TFrame", padding=padding)
    if title:
        ttk.Label(p, text=title, style="Head.TLabel").pack(anchor="w", pady=(0, 8))
    return p


def labeled_entry(parent, label, var, width=None, style="Muted.TLabel"):
    """Label above an entry inside a small frame; returns the frame."""
    f = ttk.Frame(parent, style="Panel.TFrame" if style.startswith("Panel") else "TFrame")
    ttk.Label(f, text=label, style=style).pack(anchor="w")
    ttk.Entry(f, textvariable=var, width=width).pack(fill="x")
    return f


def labeled_combo(parent, label, var, values, width=None, command=None, style="Muted.TLabel"):
    f = ttk.Frame(parent, style="Panel.TFrame" if style.startswith("Panel") else "TFrame")
    ttk.Label(f, text=label, style=style).pack(anchor="w")
    box = ttk.Combobox(f, textvariable=var, values=values, state="readonly", width=width)
    box.pack(fill="x")
    if command:
        box.bind("<<ComboboxSelected>>", lambda e: command())
    f.box = box
    return f


class DataView(ttk.Frame):
    """Backup, restore, sample data and appearance: the same in every program."""
    title = "Data"

    def __init__(self, parent, app):
        super().__init__(parent, padding=6)
        self.app = app
        ttk.Label(self, text="Your data stays on this computer:", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(self, text=app.store.path).pack(anchor="w", pady=(0, 12))
        ttk.Button(self, text="Back up to a file", width=26, command=self.backup).pack(anchor="w", pady=3)
        ttk.Button(self, text="Restore from a backup", width=26, command=self.restore).pack(anchor="w", pady=3)
        if app.SAMPLE:
            ttk.Button(self, text="Load sample data", width=26, command=self.sample).pack(anchor="w", pady=3)
        ttk.Label(self, text="Appearance", style="Muted.TLabel").pack(anchor="w", pady=(14, 2))
        self.theme = tk.StringVar(value=app.theme)
        box = ttk.Combobox(self, textvariable=self.theme, values=list(PALETTES), state="readonly", width=12)
        box.pack(anchor="w")
        box.bind("<<ComboboxSelected>>", lambda e: self.app.set_theme(self.theme.get()))
        ttk.Label(self, text="About", style="Muted.TLabel").pack(anchor="w", pady=(14, 2))
        ttk.Label(self, text="%s %s" % (app.APP_NAME, app.VERSION)).pack(anchor="w")

    def refresh(self):
        pass

    def backup(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", filetypes=[("Backup", "*.json")],
                                            initialfile="%s-backup-%s.json" % (self.app.APP_NAME.replace(" ", "-").lower(), today()))
        if path:
            self.app.commit_all()
            self.app.store.backup(path)
            self.app.status("Backup written")

    def restore(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Backup", "*.json")])
        if not path or not messagebox.askyesno("Restore", "Replace ALL current data with this backup?\nThis cannot be undone.",
                                               parent=self):
            return
        try:
            self.app.store.restore(path)
        except (ValidationError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("Restore failed", str(exc), parent=self)
            return
        self.app.rebuild("Data")
        self.app.status("Backup restored")

    def sample(self):
        if messagebox.askyesno("Sample data", "Add fictional sample records so you can explore every screen?", parent=self):
            self.app.SAMPLE(self.app.store)
            self.app.status("Sample data added")


# ---------------- main window ----------------
class BaseApp(tk.Tk):
    APP_NAME, VERSION, ACCENT = "App", "1.0.0", None
    SCHEMA, VIEWS, SAMPLE = {}, [], None
    WIDTH, HEIGHT = 1280, 830

    def __init__(self, store, start=None, theme=None):
        super().__init__()
        self.store = store
        self.title(self.APP_NAME)
        self.scale = ui_scale(self)
        w = min(int(self.WIDTH * self.scale), int(self.winfo_screenwidth() * 0.94))
        h = min(int(self.HEIGHT * self.scale), int(self.winfo_screenheight() * 0.88))
        self.geometry("%dx%d+%d+%d" % (w, h, (self.winfo_screenwidth() - w) // 2, (self.winfo_screenheight() - h) // 3))
        self.minsize(int(960 * self.scale), int(600 * self.scale))
        self._set_icon()
        self.theme = theme if theme in PALETTES else store.get_setting("theme", "Light")
        self.colors = apply_theme(self, self.theme, self.ACCENT)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.report_callback_exception = self._callback_error
        self.last_error = None
        self.navigate(start or self.titles()[0])

    def titles(self):
        return [c.title for c in self.VIEWS]

    def _set_icon(self):
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self._icon = tk.PhotoImage(file=os.path.join(base, "assets", "app.png"))
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def _build(self):
        self.side = ttk.Frame(self, style="Side.TFrame", width=int(190 * self.scale))
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        ttk.Label(self.side, text=self.APP_NAME.replace(" ", "\n", 1), style="Side.TLabel").pack(anchor="w", padx=16, pady=(18, 16))
        main = ttk.Frame(self)
        main.pack(side="left", fill="both", expand=True)
        self.heading = ttk.Label(main, text="", style="Title.TLabel")
        self.heading.pack(anchor="w", padx=18, pady=(14, 8))
        self.status_var = tk.StringVar(value="Ready. All data stays on this computer.")
        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel").pack(side="bottom", anchor="w", padx=18, pady=6)
        self.holder = ttk.Frame(main)
        self.holder.pack(fill="both", expand=True, padx=18)
        self.views, self.nav, self.current = {}, {}, None
        for title in self.titles():
            btn = ttk.Button(self.side, text=title, style="Nav.TButton", command=lambda t=title: self.navigate(t))
            btn.pack(fill="x")
            self.nav[title] = btn
        ttk.Label(self.side, text="Version %s" % self.VERSION, style="SideMuted.TLabel").pack(side="bottom", anchor="w", padx=16, pady=10)

    def view(self, title):
        if title not in self.views:
            cls = next(c for c in self.VIEWS if c.title == title)
            self.views[title] = cls(self.holder, self)
        return self.views[title]

    def navigate(self, title):
        if self.current and self.current in self.views:
            cur = self.views[self.current]
            if hasattr(cur, "commit") and cur.commit() is False:
                return
            cur.pack_forget()
        view = self.view(title)
        view.pack(fill="both", expand=True)
        self.current = title
        self.heading.configure(text=title)
        for name, btn in self.nav.items():
            btn.configure(style="NavOn.TButton" if name == title else "Nav.TButton")
        view.refresh()

    def status(self, text):
        self.status_var.set(text)

    def commit_all(self):
        return all(v.commit() is not False for v in self.views.values() if hasattr(v, "commit"))

    def rebuild(self, title):
        for v in list(self.views.values()):
            v.destroy()
        self.views, self.current = {}, None
        self.navigate(title)

    def set_theme(self, name):
        if self.commit_all():
            self.store.set_setting("theme", name)
            self.theme = name
            self.colors = apply_theme(self, name, self.ACCENT)
            self.rebuild(self.current)

    def _callback_error(self, exc, val, tb):
        import traceback
        self.last_error = "".join(traceback.format_exception(exc, val, tb))
        if sys.stderr:
            sys.stderr.write(self.last_error)
        messagebox.showerror(self.APP_NAME, "Something went wrong, but your saved data is safe.\n\n%s" % val, parent=self)

    def quit_app(self):
        if not self.commit_all() and not messagebox.askyesno(self.APP_NAME, "Some changes could not be saved. Quit anyway?", parent=self):
            return
        self.store.close()
        self.destroy()


def dpi_aware():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def selftest(app_cls):
    """Open every screen on a temporary database; exit code 0 means the program works on this machine."""
    folder = tempfile.mkdtemp(prefix="selftest-")
    store = Store(app_cls.SCHEMA, os.path.join(folder, "selftest.sqlite3"), app_cls.APP_NAME)
    if app_cls.SAMPLE:
        app_cls.SAMPLE(store)
    app = app_cls(store)
    errors = []
    app.report_callback_exception = lambda exc, val, tb: errors.append(repr(val))
    app.update()
    for title in app.titles():
        app.navigate(title)
        app.update()
    store.close()
    app.destroy()
    if errors:
        raise SystemExit("selftest failed: %s" % errors[0])
    return 0


def main(app_cls, argv=None):
    ap = argparse.ArgumentParser(prog=app_cls.APP_NAME)
    ap.add_argument("--data", help="use this database file")
    ap.add_argument("--demo", action="store_true", help="open a temporary database with sample data")
    ap.add_argument("--selftest", action="store_true", help="open every screen, then exit")
    ap.add_argument("--open", help="screen to show first")
    ap.add_argument("--theme", choices=list(PALETTES), help="colour theme for this run")
    args = ap.parse_args(argv)
    dpi_aware()
    if args.selftest:
        return selftest(app_cls)
    path = args.data
    if args.demo:
        path = os.path.join(tempfile.mkdtemp(prefix="demo-"), "demo.sqlite3")
    store = Store(app_cls.SCHEMA, path or data_path(app_cls.APP_NAME), app_cls.APP_NAME)
    if args.demo and app_cls.SAMPLE:
        app_cls.SAMPLE(store)
    app_cls(store, args.open, args.theme).mainloop()
    return 0
