#!/usr/bin/env python3
"""SnapshotAll - modern wizard-style desktop app.

Source -> Settings -> Scan (live previews, pick what to keep) -> Save
"""
import os
import sys
from pathlib import Path

# Where a downloaded Chromium (fallback only) is stored.
# The app first tries Microsoft Edge / Google Chrome, which need no download.
if getattr(sys, "frozen", False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SnapshotAll" / "browsers")
else:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

import argparse
import base64
import io
import json
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

import snapshot_all as sa
import snapshot_android as sand
import snapshot_desktop as sd
import snapshot_gif as sgif
import snapshot_live as slive
import snapshot_sandbox as sbox
from snapshot_common import APP_AUTHOR, APP_LICENSE, APP_NAME, APP_REPO, APP_URL, APP_VERSION, APP_WEBSITE

# ── Dark + orange theme ─────────────────────────────────────────────
BG = "#110D0A"
TOOLBAR = "#16110D"
SIDEBAR = "#181310"
PANEL = "#1F1813"
PANEL_H = "#2B221B"
FIELD = "#2A211A"
BORDER = "#3A2E24"
FG = "#F5EEE8"
MUTED = "#A99B8E"
ORANGE = "#F97316"
ORANGE_H = "#FB923C"
ON_ORANGE = "#1A0E05"
DANGER = "#F87171"
OK = "#4ADE80"
FAINT_TEXT = "#6E6259"

THUMB = (224, 140)
STEPS = ["Source", "Settings", "Scan", "Flows", "Save"]

ctk.set_appearance_mode("dark")


# ───────────────────────── helpers ─────────────────────────

def asset(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def open_path(p):
    p = str(p)
    try:
        if sys.platform == "win32":
            os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


def set_windows_app_id():
    """Give the process its own taskbar identity so Windows shows our icon (not python.exe's)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UsefFarahmand.SnapshotAll")
        except Exception:
            pass


def apply_window_icon(win):
    """Set the window / taskbar icon.

    CustomTkinter installs its own icon 200 ms after a window opens unless iconbitmap() was
    already called, so this must run immediately (and again later, to be safe).
    """
    try:
        if sys.platform == "win32":
            win.iconbitmap(str(asset("icon.ico")))
        else:
            win._icon_ref = tk.PhotoImage(file=str(asset("logo_64.png")))
            win.iconphoto(True, win._icon_ref)
    except Exception:
        pass


def style_titlebar(win, color=None, text=FG):
    """Paint the native Windows title bar in the app's color, like Telegram Desktop.

    Works on Windows 11 (build 22000+). Windows 10 cannot recolor the title bar, so there it just
    gets the dark variant. Does nothing on other systems.
    """
    if sys.platform != "win32":
        return
    color = color or TOOLBAR
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

        def colorref(hex_color):  # COLORREF is 0x00BBGGRR
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return r | (g << 8) | (b << 16)

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        set_attr(20, 1)                  # DWMWA_USE_IMMERSIVE_DARK_MODE (light caption buttons)
        set_attr(34, colorref(color))    # DWMWA_BORDER_COLOR
        set_attr(35, colorref(color))    # DWMWA_CAPTION_COLOR
        set_attr(36, colorref(text))     # DWMWA_TEXT_COLOR
    except Exception:
        pass


def schedule_titlebar_style(win, color=None):
    """CustomTkinter re-applies its own title bar shortly after opening, so style it a few times."""
    for ms in (60, 300, 900):
        win.after(ms, lambda: style_titlebar(win, color))


def settings_file():
    base = Path(os.environ.get("APPDATA") or (Path.home() / ".config"))
    return base / "SnapshotAll" / "settings.json"


def load_settings():
    data = {"save_dir": str(Path.home() / "Pictures" / "SnapshotAll"), "open_after_save": True}
    try:
        data.update(json.loads(settings_file().read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def store_settings(data):
    try:
        f = settings_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def make_thumb(path):
    with Image.open(path) as im:
        im.load()
        im = im.convert("RGB")
    return ImageOps.fit(im, THUMB, Image.LANCZOS, centering=(0.5, 0.0))


def short_label(label, limit=30):
    text = label
    if "://" in label:
        from urllib.parse import urlparse
        u = urlparse(label)
        text = (u.path or "/") + (f"?{u.query}" if u.query else "") + (f"#{u.fragment}" if u.fragment else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def add_edit_support(widget):
    """Make Ctrl+C/V/X/A work with any keyboard layout, and add a right-click menu."""
    w = getattr(widget, "_entry", None) or getattr(widget, "_textbox", None) or widget

    def select_all():
        if isinstance(w, tk.Text):
            w.tag_add("sel", "1.0", "end-1c")
        else:
            w.select_range(0, "end")
            w.icursor("end")

    def editable():
        return str(w.cget("state")) != "disabled"

    def on_key(e):
        if e.state & 0x4:  # Ctrl held
            k = e.keycode  # physical key code, independent of keyboard language
            if k == 86:
                if editable():
                    w.event_generate("<<Paste>>")
                return "break"
            if k == 67:
                w.event_generate("<<Copy>>")
                return "break"
            if k == 88:
                if editable():
                    w.event_generate("<<Cut>>")
                return "break"
            if k == 65:
                select_all()
                return "break"

    menu = tk.Menu(w, tearoff=0, bg=FIELD, fg=FG, activebackground=ORANGE, activeforeground=ON_ORANGE,
                   bd=0, relief="flat")
    menu.add_command(label="Paste", command=lambda: editable() and w.event_generate("<<Paste>>"))
    menu.add_command(label="Copy", command=lambda: w.event_generate("<<Copy>>"))
    menu.add_command(label="Cut", command=lambda: editable() and w.event_generate("<<Cut>>"))
    menu.add_command(label="Select all", command=select_all)
    w.bind("<Key>", on_key, add="+")
    w.bind("<Button-3>", lambda e: (w.focus_set(), menu.tk_popup(e.x_root, e.y_root)), add="+")


def bind_click(widget, fn):
    """Bind a left click on a widget and all of its children."""
    try:
        widget.bind("<Button-1>", lambda e: fn(), add="+")
    except Exception:
        pass
    for child in widget.winfo_children():
        bind_click(child, fn)


class QWriter:
    def __init__(self, q, buf):
        self.q = q
        self.buf = buf

    def write(self, s):
        if s:
            self.buf.append(s)
            self.q.put(("log", s))

    def flush(self):
        pass


# ───────────────────────── settings dialog ─────────────────────────

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app, fg_color=BG)
        apply_window_icon(self)
        self.after(300, lambda: apply_window_icon(self))
        schedule_titlebar_style(self, BG)
        self.app = app
        self.title("Settings")
        self.geometry("480x640")
        self.resizable(False, False)
        self.transient(app)
        self.columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Settings", font=app.f_title, text_color=FG).grid(
            row=0, column=0, sticky="w", padx=28, pady=(24, 14))

        gen = app._card(self)
        gen.grid(row=1, column=0, sticky="ew", padx=28)
        gen.columnconfigure(0, weight=1)
        ctk.CTkLabel(gen, text="GENERAL", font=app.f_section, text_color=ORANGE).grid(
            row=0, column=0, sticky="w", padx=18, pady=(14, 6))
        ctk.CTkLabel(gen, text="Default save location", font=app.f_small, text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=18)
        row = ctk.CTkFrame(gen, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 10))
        row.columnconfigure(0, weight=1)
        self.save_dir = tk.StringVar(value=app.settings["save_dir"])
        app._entry(row, self.save_dir).grid(row=0, column=0, sticky="ew")
        app._ghost(row, "Browse…", self.pick, width=90).grid(row=0, column=1, padx=(8, 0))
        self.open_after = tk.BooleanVar(value=app.settings["open_after_save"])
        ctk.CTkSwitch(gen, text="Open the folder after saving", variable=self.open_after, onvalue=True,
                      offvalue=False, progress_color=ORANGE, button_color=FG, button_hover_color=FG,
                      fg_color=FIELD, text_color=FG, font=app.f_body, command=self.save).grid(
            row=3, column=0, sticky="w", padx=18, pady=(0, 16))
        self.save_dir.trace_add("write", lambda *_: self.save())

        about = app._card(self)
        about.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))
        about.columnconfigure(1, weight=1)
        ctk.CTkLabel(about, text="ABOUT", font=app.f_section, text_color=ORANGE).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 8))
        ctk.CTkLabel(about, image=app.img_logo_big, text="").grid(row=1, column=0, rowspan=4, padx=(18, 14), pady=(0, 14))
        ctk.CTkLabel(about, text=APP_NAME, font=app.f_bold_lg, text_color=FG, anchor="w").grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(about, text=f"Version {APP_VERSION}", font=app.f_body, text_color=MUTED, anchor="w").grid(
            row=2, column=1, sticky="w")
        ctk.CTkLabel(about, text=f"Created by {APP_AUTHOR}", font=app.f_body, text_color=FG, anchor="w").grid(
            row=3, column=1, sticky="w")
        site = ctk.CTkLabel(about, text=APP_WEBSITE.split("://", 1)[-1].rstrip("/"), font=app.f_body,
                            text_color=ORANGE, anchor="w", cursor="hand2")
        site.grid(row=4, column=1, sticky="w", pady=(0, 14))
        site.bind("<Button-1>", lambda e: webbrowser.open(APP_WEBSITE))
        links = ctk.CTkFrame(about, fg_color="transparent")
        links.grid(row=5, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 6))
        app._ghost(links, "Website", lambda: webbrowser.open(APP_WEBSITE), width=100).pack(side="left")
        app._ghost(links, "GitHub", lambda: webbrowser.open(APP_URL), width=100).pack(side="left", padx=8)
        app._ghost(links, "Project page", lambda: webbrowser.open(APP_REPO), width=120).pack(side="left")
        ctk.CTkLabel(about, text=f"Released under the {APP_LICENSE}.", font=app.f_small, text_color=MUTED).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 16))

        app._primary(self, "Done", self.destroy, width=110).grid(row=3, column=0, sticky="e", padx=28, pady=(18, 0))
        self.after(150, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.focus_set()
        except Exception:
            pass

    def pick(self):
        f = filedialog.askdirectory(parent=self)
        if f:
            self.save_dir.set(f)

    def save(self):
        self.app.settings["save_dir"] = self.save_dir.get().strip() or self.app.settings["save_dir"]
        self.app.settings["open_after_save"] = bool(self.open_after.get())
        store_settings(self.app.settings)


# ───────────────────────── main window ─────────────────────────

class App(ctk.CTk):
    def __init__(self):
        set_windows_app_id()
        super().__init__(fg_color=BG)
        apply_window_icon(self)
        self.title(f"{APP_NAME} - Screenshot Every Page & Screen")
        self.geometry("1100x740")
        self.minsize(1000, 680)

        # fonts
        fam = "Segoe UI"
        self.f_title = ctk.CTkFont(family=fam, size=26, weight="bold")
        self.f_bold_lg = ctk.CTkFont(family=fam, size=18, weight="bold")
        self.f_bold = ctk.CTkFont(family=fam, size=14, weight="bold")
        self.f_body = ctk.CTkFont(family=fam, size=14)
        self.f_small = ctk.CTkFont(family=fam, size=12)
        self.f_section = ctk.CTkFont(family=fam, size=11, weight="bold")

        # images
        def img(name, size):
            im = Image.open(asset(name))
            return ctk.CTkImage(light_image=im, dark_image=im, size=size)
        self.img_logo = img("logo_64.png", (36, 36))
        self.img_logo_big = img("logo.png", (64, 64))
        self.img_web = img("icon_web.png", (52, 52))
        self.img_app = img("icon_app.png", (52, 52))
        self.img_gear = img("icon_gear.png", (20, 20))

        self.settings = load_settings()
        self.q = queue.Queue()
        self.buf = []
        self.temp = None
        self.items = []
        self.scanning = False
        self.thread = None
        self.pending_nav = None
        self.current = 0
        self.ncols = 3
        self.limit = 50
        self.noun = "pages"
        self.job = None
        self.settings_win = None
        self.details_visible = False
        self.saved_dir = None

        # state variables
        self.source = tk.StringVar(value="web")
        self.web_url = tk.StringVar()
        self.app_file = tk.StringVar()
        self.attach_mode = tk.BooleanVar(value=False)
        self.attach_title = tk.StringVar()
        self.max_pages = tk.StringVar(value="50")
        self.depth_web = tk.StringVar(value="5")
        self.delay_web = tk.StringVar(value="1.0")
        self.viewport = tk.StringVar(value="Desktop")
        self.width = tk.StringVar(value="1440")
        self.height = tk.StringVar(value="900")
        self.session = tk.StringVar()
        self.tidy_floating = tk.BooleanVar(value=True)
        self.max_screens = tk.StringVar(value="40")
        self.depth_app = tk.StringVar(value="3")
        self.max_clicks = tk.StringVar(value="25")
        self.delay_app = tk.StringVar(value="1.0")
        self.scroll = tk.StringVar(value="3")
        self.serial = tk.StringVar()
        self.package = tk.StringVar()
        self.avoid = tk.StringVar(value=sa.DEFAULT_AVOID)
        self.android_runtime = tk.StringVar(value="Automatic")   # Automatic | My device
        self.show_window = tk.BooleanVar(value=False)            # Windows programs: visible window + real mouse
        self.save_dir = tk.StringVar(value=self.settings["save_dir"])
        self.save_png = tk.BooleanVar(value=True)
        self.make_gif = tk.BooleanVar(value=False)
        self.gif_seconds = tk.StringVar(value="1.6")
        self.gif_style = tk.StringVar(value="Slideshow")
        self.gif_quality = tk.StringVar(value="Standard")
        self.gif_wm = tk.BooleanVar(value=True)
        self.flow_steps = tk.StringVar(value="5")
        self.flow_max = tk.StringVar(value="12")
        self.flows = []             # editable flows: {"on": BooleanVar, "steps": [item labels]}
        self._flows_sig = None      # selection the flows were generated from
        self._flow_cards = []       # one card (frame) per flow
        self._tile_photos = {}      # (page label, size) -> PhotoImage, cached
        self.scan_ns = None         # settings of the last scan (used by live recording)

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_toolbar()
        self._build_body()
        self.set_source("web")
        self.go(0)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self.poll)
        self.after(300, lambda: apply_window_icon(self))
        schedule_titlebar_style(self, TOOLBAR)

    # ───────────── reusable widgets ─────────────
    def _card(self, parent, **kw):
        kw.setdefault("border_width", 1)
        return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=16, border_color=BORDER, **kw)

    def _primary(self, parent, text, command, width=150, **kw):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=42, corner_radius=12,
                             fg_color=ORANGE, hover_color=ORANGE_H, text_color=ON_ORANGE,
                             text_color_disabled="#6B4A2E", font=self.f_bold, **kw)

    def _ghost(self, parent, text, command, width=110, height=42, **kw):
        kw.setdefault("border_width", 1)
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=height, corner_radius=12,
                             fg_color="transparent", hover_color=PANEL_H, border_color=BORDER,
                             text_color=FG, text_color_disabled="#6E6259", font=self.f_body, **kw)

    def _entry(self, parent, var, placeholder="", **kw):
        kw.setdefault("height", 42)
        e = ctk.CTkEntry(parent, textvariable=var, corner_radius=10, fg_color=FIELD,
                         border_color=BORDER, border_width=1, text_color=FG, font=self.f_body, **kw)
        add_edit_support(e)
        if placeholder:  # CTkEntry ignores placeholder_text when a textvariable is used, so draw our own
            hint = tk.Label(e, text=placeholder, bg=FIELD, fg=MUTED, font=("Segoe UI", 10), bd=0, cursor="xterm")
            hint.bind("<Button-1>", lambda _e: e.focus_set())

            def sync(*_):
                if var.get():
                    hint.place_forget()
                else:
                    hint.place(x=14, rely=0.5, anchor="w")
            var.trace_add("write", sync)
            sync()
        return e

    def _circle(self, parent, text, size=30, fg=FIELD, text_color=MUTED, font=None):
        """A perfectly round badge (frame + centered label). Returns (frame, label)."""
        f = ctk.CTkFrame(parent, width=size, height=size, corner_radius=size // 2, fg_color=fg)
        lbl = ctk.CTkLabel(f, text=text, text_color=text_color, font=font or self.f_bold, fg_color="transparent")
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        return f, lbl

    def _header(self, page, title, subtitle):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=36, pady=(28, 14))
        t = ctk.CTkLabel(head, text=title, font=self.f_title, text_color=FG, anchor="w")
        t.pack(anchor="w")
        s = ctk.CTkLabel(head, text=subtitle, font=self.f_body, text_color=MUTED, anchor="w", justify="left")
        s.pack(anchor="w", pady=(4, 0))
        return t, s

    def _footer(self, page, back=None, next_text="Next  →", next_cmd=None):
        foot = ctk.CTkFrame(page, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=36, pady=(12, 24))
        foot.grid_columnconfigure(1, weight=1)
        back_btn = None
        if back:
            back_btn = self._ghost(foot, "←  Back", back, width=110)
            back_btn.grid(row=0, column=0)
        next_btn = self._primary(foot, next_text, next_cmd, width=170)
        next_btn.grid(row=0, column=2)
        return back_btn, next_btn

    # ───────────── toolbar / sidebar / pages ─────────────
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color=TOOLBAR, corner_radius=0, height=64)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(bar, image=self.img_logo, text="").grid(row=0, column=0, padx=(22, 10), pady=12)
        ctk.CTkLabel(bar, text=APP_NAME, font=self.f_bold_lg, text_color=FG).grid(row=0, column=1)
        ctk.CTkLabel(bar, text=f"v{APP_VERSION}", font=self.f_small, text_color=MUTED, fg_color=PANEL,
                     corner_radius=8, width=56, height=22).grid(row=0, column=2, padx=10)
        self._ghost(bar, "New capture", self.reset_all, width=120, height=36, border_width=0).grid(
            row=0, column=4, padx=4)
        ctk.CTkButton(bar, text="Settings", image=self.img_gear, compound="left", command=self.open_settings,
                      width=120, height=36, corner_radius=12, fg_color="transparent", hover_color=PANEL_H,
                      text_color=FG, font=self.f_body).grid(row=0, column=5, padx=(4, 18))
        ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0).grid(row=1, column=0, sticky="ew")

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        side = ctk.CTkFrame(body, fg_color=SIDEBAR, corner_radius=0, width=230)
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)
        ctk.CTkLabel(side, text="NEW CAPTURE", font=self.f_section, text_color=MUTED).pack(
            anchor="w", padx=28, pady=(30, 14))
        self.step_widgets = []
        for i, name in enumerate(STEPS):
            row = ctk.CTkFrame(side, fg_color="transparent")
            row.pack(anchor="w", padx=24)
            circle, clabel = self._circle(row, str(i + 1))
            circle.pack(side="left")
            label = ctk.CTkLabel(row, text=name, font=self.f_body, text_color=MUTED)
            label.pack(side="left", padx=12)
            line = None
            if i < len(STEPS) - 1:
                line = ctk.CTkFrame(side, width=2, height=26, fg_color=BORDER, corner_radius=1)
                line.pack(anchor="w", padx=(38, 0))
            self.step_widgets.append((circle, clabel, label, line))

        self.content = ctk.CTkFrame(body, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = []
        for _ in range(5):
            p = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
            p.grid(row=0, column=0, sticky="nsew")
            self.pages.append(p)
        self._build_source_page()
        self._build_settings_page()
        self._build_scan_page()
        self._build_flows_page()
        self._build_save_page()

    def go(self, n):
        self.current = n
        self.pages[n].tkraise()
        for i, (circle, clabel, label, line) in enumerate(self.step_widgets):
            if i < n:
                circle.configure(fg_color=ORANGE)
                clabel.configure(text="✓", text_color=ON_ORANGE)
                label.configure(text_color=FG, font=self.f_body)
            elif i == n:
                circle.configure(fg_color=ORANGE)
                clabel.configure(text=str(i + 1), text_color=ON_ORANGE)
                label.configure(text_color=ORANGE, font=self.f_bold)
            else:
                circle.configure(fg_color=FIELD)
                clabel.configure(text=str(i + 1), text_color=MUTED)
                label.configure(text_color=MUTED, font=self.f_body)
            if line is not None:
                line.configure(fg_color=ORANGE if i < n else BORDER)

    def open_settings(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.focus_set()
            return
        self.settings_win = SettingsDialog(self)

    # ───────────── page 0: source ─────────────
    def _build_source_page(self):
        page = self.pages[0]
        self._header(page, "What do you want to capture?",
                     "Choose a source and enter its address. SnapshotAll finds every page or screen for you.")
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=36)
        body.grid_columnconfigure((0, 1), weight=1, uniform="cards")

        self.src_cards = {}
        for col, (key, image, title, desc) in enumerate([
            ("web", self.img_web, "Website", "Any online site, or a local server such as localhost:3000"),
            ("app", self.img_app, "App", "An Android .apk, or a Windows .exe / .jar program"),
        ]):
            card = self._card(body, border_width=2)
            card.grid(row=0, column=col, sticky="ew", padx=(0, 8) if col == 0 else (8, 0), pady=(4, 16))
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, image=image, text="").grid(row=0, column=0, rowspan=2, padx=(20, 16), pady=20)
            ctk.CTkLabel(card, text=title, font=self.f_bold_lg, text_color=FG, anchor="w").grid(
                row=0, column=1, sticky="sw", pady=(18, 0))
            ctk.CTkLabel(card, text=desc, font=self.f_small, text_color=MUTED, anchor="w", justify="left",
                         wraplength=220).grid(row=1, column=1, sticky="nw", pady=(2, 18), padx=(0, 14))
            bind_click(card, lambda k=key: self.set_source(k))
            self.src_cards[key] = card

        panel = self._card(body)
        panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        panel.grid_columnconfigure(0, weight=1)

        # website input
        self.web_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.web_frame.grid(row=0, column=0, sticky="ew", padx=22, pady=20)
        self.web_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.web_frame, text="WEBSITE ADDRESS", font=self.f_section, text_color=ORANGE).grid(
            row=0, column=0, sticky="w")
        row = ctk.CTkFrame(self.web_frame, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        row.grid_columnconfigure(0, weight=1)
        self._entry(row, self.web_url, "example.com   ·   https://www.example.com   ·   localhost:3000").grid(
            row=0, column=0, sticky="ew")
        self._ghost(row, "Paste", lambda: self.paste_into(self.web_url), width=90).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkLabel(self.web_frame, text="https:// and www. are optional. Works with any public website and with local development servers.",
                     font=self.f_small, text_color=MUTED).grid(row=2, column=0, sticky="w")

        # app input
        self.app_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.app_frame.grid(row=0, column=0, sticky="ew", padx=22, pady=20)
        self.app_frame.grid_columnconfigure(0, weight=1)
        self.app_label = ctk.CTkLabel(self.app_frame, text="APP FILE", font=self.f_section, text_color=ORANGE)
        self.app_label.grid(row=0, column=0, sticky="w")
        self.file_row = ctk.CTkFrame(self.app_frame, fg_color="transparent")
        self.file_row.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.file_row.grid_columnconfigure(0, weight=1)
        self._entry(self.file_row, self.app_file, "Choose an .apk, .exe or .jar file").grid(row=0, column=0, sticky="ew")
        self._ghost(self.file_row, "Browse…", self.pick_app, width=100).grid(row=0, column=1, padx=(8, 0))
        self._ghost(self.file_row, "Paste", lambda: self.paste_into(self.app_file), width=90).grid(
            row=0, column=2, padx=(8, 0))
        self.attach_row = ctk.CTkFrame(self.app_frame, fg_color="transparent")
        self.attach_row.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.attach_row.grid_columnconfigure(0, weight=1)
        self._entry(self.attach_row, self.attach_title, "Part of the window title, e.g. Notepad").grid(
            row=0, column=0, sticky="ew")
        self._ghost(self.attach_row, "Paste", lambda: self.paste_into(self.attach_title), width=90).grid(
            row=0, column=1, padx=(8, 0))
        ctk.CTkSwitch(self.app_frame, text="Attach to a program that is already running (Windows)",
                      variable=self.attach_mode, onvalue=True, offvalue=False, command=self._toggle_attach,
                      progress_color=ORANGE, button_color=FG, button_hover_color=FG, fg_color=FIELD,
                      text_color=FG, font=self.f_body).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.src_err = ctk.CTkLabel(body, text="", font=self.f_body, text_color=DANGER, anchor="w")
        self.src_err.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._footer(page, None, "Next  →", self.source_next)

    def set_source(self, key):
        self.source.set(key)
        for k, card in self.src_cards.items():
            card.configure(border_color=ORANGE if k == key else BORDER, fg_color=PANEL_H if k == key else PANEL)
        if key == "web":
            self.app_frame.grid_remove()
            self.web_frame.grid()
        else:
            self.web_frame.grid_remove()
            self.app_frame.grid()
            self._toggle_attach()
        self.src_err.configure(text="")

    def _toggle_attach(self):
        if self.attach_mode.get():
            self.file_row.grid_remove()
            self.attach_row.grid()
            self.app_label.configure(text="WINDOW TITLE")
        else:
            self.attach_row.grid_remove()
            self.file_row.grid()
            self.app_label.configure(text="APP FILE")

    def paste_into(self, var):
        try:
            var.set(self.clipboard_get().strip().strip('"'))
        except tk.TclError:
            pass

    def pick_app(self):
        f = filedialog.askopenfilename(filetypes=[("Apps", "*.apk *.exe *.jar"), ("Android APK", "*.apk"),
                                                  ("Windows app", "*.exe"), ("Java app", "*.jar")])
        if f:
            self.app_file.set(f)

    def _resolve_source(self):
        """Return (mode, target, label) or None after showing an inline error."""
        if self.source.get() == "web":
            t = self.web_url.get().strip()
            if not t:
                self.src_err.configure(text="Enter a website address to continue.")
                return None
            return "web", t, t
        if self.attach_mode.get():
            t = self.attach_title.get().strip()
            if not t:
                self.src_err.configure(text="Enter part of the window title to attach to.")
                return None
            return "desktop", "", t
        t = self.app_file.get().strip().strip('"')
        low = t.lower()
        if not t:
            self.src_err.configure(text="Choose an .apk, .exe or .jar file to continue.")
            return None
        if not low.endswith((".apk", ".exe", ".jar")):
            self.src_err.configure(text="Unsupported file. Supported app files: .apk, .exe, .jar.")
            return None
        if not Path(t).exists():
            self.src_err.configure(text="That file does not exist.")
            return None
        return ("apk" if low.endswith(".apk") else "desktop"), t, Path(t).stem

    def source_next(self):
        self.src_err.configure(text="")
        job = self._resolve_source()
        if not job:
            return
        self.job = job
        self._fill_settings()
        self.go(1)

    # ───────────── page 1: settings ─────────────
    def _build_settings_page(self):
        page = self.pages[1]
        self.set_title, self.set_sub = self._header(page, "Settings", "")
        self.set_body = ctk.CTkFrame(page, fg_color="transparent")
        self.set_body.grid(row=1, column=0, sticky="nsew", padx=36)
        self.set_body.grid_columnconfigure(0, weight=1)
        self._footer(page, lambda: self.go(0), "Start scan  ▶", self.start_scan)

    def _field(self, parent, row, col, label, var, width=150):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=col, sticky="w", padx=(0, 28), pady=(0, 14))
        ctk.CTkLabel(f, text=label, font=self.f_small, text_color=MUTED, anchor="w").pack(anchor="w")
        e = self._entry(f, var, width=width)
        e.pack(anchor="w", pady=(4, 0))
        return e

    def _section(self, parent, text, row):
        ctk.CTkLabel(parent, text=text, font=self.f_section, text_color=ORANGE).grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(4, 10))

    def _fill_settings(self):
        mode, target, label = self.job
        for w in self.set_body.winfo_children():
            w.destroy()
        card = self._card(self.set_body)
        card.grid(row=0, column=0, sticky="ew")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)

        if mode == "web":
            self.set_title.configure(text="Website settings")
            self.set_sub.configure(text=f"Scanning  {label}")
            self._section(inner, "CRAWLING", 0)
            self._field(inner, 1, 0, "Max pages", self.max_pages)
            self._field(inner, 1, 1, "Crawl depth", self.depth_web)
            self._field(inner, 1, 2, "Wait per page (s)", self.delay_web)
            self._section(inner, "BROWSER WINDOW", 2)
            seg = ctk.CTkSegmentedButton(inner, values=["Desktop", "Mobile"], variable=self.viewport,
                                         command=lambda _v: self._sync_viewport(), selected_color=ORANGE,
                                         selected_hover_color=ORANGE_H, unselected_color=FIELD,
                                         unselected_hover_color=PANEL_H, fg_color=FIELD, text_color=FG,
                                         font=self.f_body, height=38, corner_radius=10)
            seg.grid(row=3, column=0, sticky="w", pady=(0, 14), padx=(0, 28))
            self.w_entry = self._field(inner, 3, 1, "Width", self.width, 110)
            self.h_entry = self._field(inner, 3, 2, "Height", self.height, 110)
            self._sync_viewport()
            self._section(inner, "PAGE CLEAN-UP", 4)
            ctk.CTkSwitch(inner, text="Tidy floating bars  (sticky footers, cookie banners, chat bubbles)",
                          variable=self.tidy_floating, onvalue=True, offvalue=False, progress_color=ORANGE,
                          button_color=FG, button_hover_color=FG, fg_color=FIELD, text_color=FG,
                          font=self.f_body).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 14))
            self._section(inner, "PAGES BEHIND A LOGIN  (OPTIONAL)", 6)
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.grid(row=7, column=0, columnspan=4, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            self._entry(row, self.session, "Playwright session file (.json)").grid(row=0, column=0, sticky="ew")
            self._ghost(row, "Browse…", self.pick_session, width=100).grid(row=0, column=1, padx=(8, 0))
        else:
            android = mode == "apk"
            self.set_title.configure(text="Android app settings" if android else "Windows app settings")
            self.set_sub.configure(text=f"Exploring  {label}")
            self._section(inner, "EXPLORATION", 0)
            self._field(inner, 1, 0, "Max screens", self.max_screens)
            self._field(inner, 1, 1, "Exploration depth", self.depth_app)
            self._field(inner, 1, 2, "Max taps per screen", self.max_clicks)
            self._field(inner, 1, 3, "Wait per tap (s)", self.delay_app, 120)
            if android:
                self._section(inner, "ANDROID", 2)
                self._field(inner, 3, 0, "Scroll steps (0 = off)", self.scroll)
                self._field(inner, 3, 1, "Device serial (optional)", self.serial, 150)
                self._field(inner, 3, 2, "Package name (optional)", self.package, 170)
                rt = ctk.CTkSegmentedButton(inner, values=["Automatic", "My device"], variable=self.android_runtime,
                                            selected_color=ORANGE, selected_hover_color=ORANGE_H,
                                            unselected_color=FIELD, unselected_hover_color=PANEL_H, fg_color=FIELD,
                                            text_color=FG, font=self.f_body, height=38, corner_radius=10)
                rt.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 10))
                note = ("Automatic: the APK runs on a virtual phone that SnapshotAll starts in the background - "
                        "no emulator or phone to open yourself. The first run downloads the Android runtime "
                        "(about 2 GB, one time). A phone or emulator that is already connected is used instead "
                        "when there is one. My device: use only a connected phone / emulator.")
            else:
                self._section(inner, "WINDOWS", 2)
                ctk.CTkSwitch(inner, text="Show the program's window while it is captured  (uses your mouse)",
                              variable=self.show_window, onvalue=True, offvalue=False, progress_color=ORANGE,
                              button_color=FG, button_hover_color=FG, fg_color=FIELD, text_color=FG,
                              font=self.f_body).grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 10))
                note = ("By default the program runs in the background: its windows are invisible, it can not "
                        "take focus and your mouse is never used. Controls are pressed through Windows UI "
                        "Automation; the program is restarted for each branch and always closed at the end.")
            ctk.CTkLabel(inner, text=note, font=self.f_small, text_color=MUTED, anchor="w", justify="left",
                         wraplength=640).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 12))
            self._section(inner, "SAFETY", 6)
            ctk.CTkLabel(inner, text="Never tap / click buttons matching (regex)", font=self.f_small,
                         text_color=MUTED).grid(row=7, column=0, columnspan=4, sticky="w")
            self._entry(inner, self.avoid).grid(row=8, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        inner.grid_columnconfigure(3, weight=1)

    def _sync_viewport(self):
        state = "disabled" if self.viewport.get() == "Mobile" else "normal"
        self.w_entry.configure(state=state)
        self.h_entry.configure(state=state)

    def pick_session(self):
        f = filedialog.askopenfilename(filetypes=[("Session JSON", "*.json"), ("All files", "*.*")])
        if f:
            self.session.set(f)

    def _num(self, var, default, cast=int, lo=0):
        try:
            v = cast(str(var.get()).strip())
        except Exception:
            return default
        return max(v, lo)

    # ───────────── page 2: scan ─────────────
    def _build_scan_page(self):
        page = self.pages[2]
        self.scan_title, self.scan_sub = self._header(page, "Scanning…", "")
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=36)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(4, weight=1)

        self.bar = ctk.CTkProgressBar(body, height=8, corner_radius=4, fg_color=FIELD, progress_color=ORANGE)
        self.bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        info = ctk.CTkFrame(body, fg_color="transparent")
        info.grid(row=1, column=0, sticky="ew")
        info.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(info, text="", font=self.f_small, text_color=MUTED, anchor="w")
        self.status.grid(row=0, column=0, sticky="w")
        self.found = ctk.CTkLabel(info, text="", font=self.f_bold, text_color=ORANGE)
        self.found.grid(row=0, column=1, sticky="e")

        tools = ctk.CTkFrame(body, fg_color="transparent")
        tools.grid(row=2, column=0, sticky="ew", pady=(10, 8))
        tools.grid_columnconfigure(3, weight=1)
        self._ghost(tools, "Select all", lambda: self.select_all(True), width=90, height=34).grid(row=0, column=0)
        self._ghost(tools, "Select none", lambda: self.select_all(False), width=100, height=34).grid(
            row=0, column=1, padx=8)
        self.sel_lbl = ctk.CTkLabel(tools, text="", font=self.f_body, text_color=FG)
        self.sel_lbl.grid(row=0, column=2, padx=8)
        self.details_btn = self._ghost(tools, "Details", self.toggle_details, width=80, height=34)
        self.details_btn.grid(row=0, column=4, padx=(0, 8))
        self.stop_btn = ctk.CTkButton(tools, text="■  Stop scan", command=self.stop_scan, width=120, height=34,
                                      corner_radius=12, fg_color="transparent", hover_color=PANEL_H,
                                      border_width=1, border_color=ORANGE, text_color=ORANGE, font=self.f_body)
        self.stop_btn.grid(row=0, column=5)

        self.details = ctk.CTkTextbox(body, height=120, fg_color="#0F0C0A", text_color="#EADFD3", corner_radius=10,
                                      border_width=1, border_color=BORDER, font=ctk.CTkFont(family="Consolas", size=12))
        add_edit_support(self.details)
        self.copy_btn = self._ghost(body, "Copy details", self.copy_details, width=110, height=30)

        self.scroll_frame = ctk.CTkScrollableFrame(body, fg_color="transparent", scrollbar_button_color=BORDER,
                                                   scrollbar_button_hover_color=ORANGE)
        self.scroll_frame.grid(row=4, column=0, sticky="nsew")
        self.back_btn, self.next_btn = self._footer(page, self.back_from_scan, "Next  →", self.scan_next)

    def _reset_scan_ui(self):
        for it in self.items:
            it["card"].destroy()
        self.items = []
        self.buf.clear()
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")
        self._hide_details()
        self.scan_title.configure(text="Scanning…")
        self.found.configure(text="")
        self.status.configure(text="Starting…")
        self.sel_lbl.configure(text="")
        self.bar.configure(mode="indeterminate")
        self.bar.start()
        self.stop_btn.grid()
        self.next_btn.configure(state="disabled")

    def _confirm_run(self, mode, target):
        """Ask before a program is run on this PC. Returns (go_ahead, accept_android_licenses)."""
        if mode == "desktop" and not target:                      # attaching to a program that is already running
            return True, False
        if mode == "desktop":
            extra = ""
            if sbox.looks_like_installer(target):
                extra = ("\n\nThe file name looks like an INSTALLER or UPDATER. Running it can install, change or "
                         "remove software on this PC - the background mode does not prevent that.")
            return messagebox.askyesno(
                "Run this program?",
                "SnapshotAll will run this program on your PC, in the background, to capture its windows. "
                "It is closed again at the end.\n\nThe program still runs with your user rights, so only use "
                f"files you trust.{extra}\n\nContinue?", icon="warning", parent=self), False
        if self.android_runtime.get() == "My device":
            return True, False
        missing = sand.Paths().missing()
        if not missing:
            return True, True
        ok = messagebox.askyesno(
            "Download the Android runtime?",
            "To run the APK without any other app, SnapshotAll sets up its own virtual phone. This is a one-time "
            "download of about 2 GB:\n\n  - " + "\n  - ".join(missing) + "\n\nIt is stored in your user folder "
            f"and used only by SnapshotAll. By continuing you accept the Android SDK License:\n{sand.LICENSE_URL}"
            "\n\nThe emulator needs hardware virtualisation (Windows Hypervisor Platform).\n\nContinue?",
            parent=self)
        return ok, ok

    def start_scan(self):
        mode, target, label = self.job
        if mode != "web":
            go_ahead, accept_licenses = self._confirm_run(mode, target)
            if not go_ahead:
                return
        else:
            accept_licenses = False
        self._cleanup_temp()
        self.temp = Path(tempfile.mkdtemp(prefix="snapshotall_"))
        is_web = mode == "web"
        self.limit = self._num(self.max_pages if is_web else self.max_screens, 50 if is_web else 40, lo=1)
        self.flows, self._flows_sig = [], None
        self.noun = "pages" if is_web else "screens"
        ns = argparse.Namespace(
            max_depth=self._num(self.depth_web if is_web else self.depth_app, 5 if is_web else 3),
            delay=self._num(self.delay_web if is_web else self.delay_app, 1.0, float),
            max_pages=self.limit, width=self._num(self.width, 1440, lo=320), height=self._num(self.height, 900, lo=240),
            mobile=self.viewport.get() == "Mobile", storage_state=self.session.get().strip() or None,
            keep_floating=not self.tidy_floating.get(),
            max_screens=self.limit, max_clicks=self._num(self.max_clicks, 25, lo=1), scroll=self._num(self.scroll, 3),
            serial=self.serial.get().strip() or None, package=self.package.get().strip() or None,
            avoid=self.avoid.get().strip(), attach=label if (mode == "desktop" and not target) else None,
            android_runtime="device" if self.android_runtime.get() == "My device" else "auto",
            accept_android_licenses=accept_licenses, visible=self.show_window.get(),
            on_item=self._on_item,
        )
        runner = {"web": sa.run_web, "apk": sa.run_apk, "desktop": sd.run_desktop}[mode]
        self.scan_ns = ns
        self._reset_scan_ui()
        self.scan_sub.configure(text=f"{label}")
        self.scanning = True
        self.pending_nav = None
        sa.STOP.clear()
        self.thread = threading.Thread(target=self._worker, args=(runner, target, self.temp, ns), daemon=True)
        self.thread.start()
        self.go(2)

    def _worker(self, runner, target, out, ns):
        old = (sys.stdout, sys.stderr)
        sys.stdout = sys.stderr = QWriter(self.q, self.buf)
        failed = False
        try:
            try:
                runner(target, out, ns)
            except sa.BrowserUnavailable as e:
                print(f"No usable browser found ({e}).")
                print("Downloading Chromium (one-time, about 150 MB)…")
                sa.install_chromium()
                runner(target, out, ns)
        except SystemExit as e:
            if e.code:
                print(e.code)
                failed = True
        except Exception:
            print("\n===== ERROR =====")
            print(traceback.format_exc())
            failed = True
        finally:
            sys.stdout, sys.stderr = old
            self.q.put(("done", failed))

    def _on_item(self, path, label, parent=None, title="", kind="screen", links=None):  # worker thread
        try:
            thumb = make_thumb(path)
        except Exception:
            thumb = None
        self.q.put(("item", path, label, thumb, parent, title, kind, links or []))

    def _add_card(self, path, label, thumb, parent=None, title="", kind="screen", links=None):
        if self.bar.cget("mode") == "indeterminate":
            self.bar.stop()
            self.bar.configure(mode="determinate")
        idx = len(self.items) + 1
        var = tk.BooleanVar(value=True)
        card = ctk.CTkFrame(self.scroll_frame, fg_color=PANEL, corner_radius=14, border_width=2, border_color=ORANGE)
        item = {"path": Path(path), "label": label, "var": var, "card": card, "thumb": thumb,
                "parent": parent, "title": title, "kind": kind, "links": links or []}
        if thumb is not None:
            item["img"] = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
            pic = ctk.CTkLabel(card, image=item["img"], text="")
        else:
            pic = ctk.CTkLabel(card, text="(no preview)", width=THUMB[0], height=THUMB[1], text_color=MUTED)
        pic.pack(padx=10, pady=(10, 4))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 10))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkCheckBox(row, text=f"{idx}. {short_label(label, 20)}", variable=var, font=self.f_small,
                        text_color=FG, fg_color=ORANGE, hover_color=ORANGE_H, border_color=MUTED,
                        checkmark_color=ON_ORANGE, corner_radius=6, checkbox_width=20, checkbox_height=20,
                        command=lambda: self._card_changed(item)).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(row, text="View", width=46, height=26, corner_radius=8, fg_color=FIELD,
                      hover_color=PANEL_H, text_color=FG, font=self.f_small,
                      command=lambda p=item["path"]: open_path(p)).grid(row=0, column=1)
        pic.bind("<Button-1>", lambda e, it=item: self._toggle_item(it))
        self.items.append(item)
        self._place(item, len(self.items) - 1)
        self.found.configure(text=f"{len(self.items)} {self.noun} found")
        self.bar.set(min(len(self.items) / max(self.limit, 1), 1.0))
        self._update_selection()

    def _place(self, item, i):
        n = self.ncols
        item["card"].grid(row=i // n, column=i % n, padx=8, pady=8, sticky="n")

    def _regrid(self):
        for i, it in enumerate(self.items):
            self._place(it, i)

    def _maybe_reflow(self):
        if self.current == 3 and self.flows:                      # re-wrap the flow tiles when the width changes
            per_row = self._tiles_per_row()
            known = getattr(self, "_flow_per_row", None)
            self._flow_per_row = per_row
            if known is not None and per_row != known:
                self._render_flows()
        if self.current != 2:
            return
        w = self.scroll_frame.winfo_width()
        n = max(1, min(6, (w - 40) // 252))
        if w > 100 and n != self.ncols:
            self.ncols = n
            self._regrid()

    def _toggle_item(self, item):
        item["var"].set(not item["var"].get())
        self._card_changed(item)

    def _card_changed(self, item):
        item["card"].configure(border_color=ORANGE if item["var"].get() else BORDER)
        self._update_selection()

    def select_all(self, value):
        for it in self.items:
            it["var"].set(value)
            it["card"].configure(border_color=ORANGE if value else BORDER)
        self._update_selection()

    def selected(self):
        return [it for it in self.items if it["var"].get()]

    def _update_selection(self):
        n = len(self.selected())
        self.sel_lbl.configure(text=f"{n} of {len(self.items)} selected")
        self.next_btn.configure(state="normal" if (n > 0 and not self.scanning) else "disabled")

    def stop_scan(self):
        sa.STOP.set()
        self.status.configure(text="Stopping…")
        self.stop_btn.configure(state="disabled")

    def back_from_scan(self):
        if self.scanning:
            self.pending_nav = 1
            self.stop_scan()
        else:
            self.go(1)

    def scan_next(self):
        if not self.selected():
            return
        signature = tuple(it["label"] for it in self.selected())
        if signature != self._flows_sig:             # keep the user's edits while the selection is unchanged
            self._generate_flows()
        self._render_flows()
        self.go(3)

    def _scan_done(self, failed):
        self.scanning = False
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1.0 if self.items else 0)
        self.stop_btn.grid_remove()
        self.stop_btn.configure(state="normal")
        stopped = sa.STOP.is_set()
        if self.pending_nav is not None:
            nav, self.pending_nav = self.pending_nav, None
            self.go(nav)
            return
        if failed:
            self.scan_title.configure(text="The scan hit a problem")
            self.status.configure(text="See the details below." if self.items else "Nothing could be captured.")
            self._show_details()
        elif not self.items:
            self.scan_title.configure(text="Nothing was found")
            self.status.configure(text="Try a different address or settings.")
        else:
            self.scan_title.configure(text="Scan stopped" if stopped else "Scan complete")
            self.status.configure(text="Choose the ones you want to keep, then press Next.")
        self._update_selection()

    # details panel (never saved to disk)
    def toggle_details(self):
        if self.details_visible:
            self._hide_details()
        else:
            self._show_details()

    def _show_details(self):
        self.details_visible = True
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", "".join(self.buf))
        self.details.see("end")
        self.details.configure(state="disabled")
        self.details.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        self.copy_btn.grid(row=5, column=0, sticky="e", pady=(6, 0))

    def _hide_details(self):
        self.details_visible = False
        self.details.grid_remove()
        self.copy_btn.grid_remove()

    def copy_details(self):
        self.clipboard_clear()
        self.clipboard_append("".join(self.buf))

    # ───────────── page 3: flows ─────────────
    def _build_flows_page(self):
        page = self.pages[3]
        self._header(page, "Review your flows",
                     "Each flow becomes one GIF. Reorder or remove steps, add pages, or build your own flow.")
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=36)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        tools = ctk.CTkFrame(body, fg_color="transparent")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for label, var in (("Steps per flow", self.flow_steps), ("Max flows", self.flow_max)):
            ctk.CTkLabel(tools, text=label, font=self.f_small, text_color=MUTED).pack(side="left", padx=(0, 8))
            self._entry(tools, var, width=60, height=34).pack(side="left", padx=(0, 18))
        self._ghost(tools, "Regenerate", self._regen_flows, width=110, height=34).pack(side="left", padx=(0, 8))
        self._ghost(tools, "+ New flow", self._new_flow, width=110, height=34).pack(side="left")
        self.flow_count = ctk.CTkLabel(tools, text="", font=self.f_body, text_color=FG)
        self.flow_count.pack(side="right")
        self.flow_list = ctk.CTkScrollableFrame(body, fg_color="transparent", scrollbar_button_color=BORDER,
                                                scrollbar_button_hover_color=ORANGE)
        self.flow_list.grid(row=1, column=0, sticky="nsew")
        self.flow_list.grid_columnconfigure(0, weight=1)
        self._footer(page, lambda: self.go(2), "Next  →", self.flows_next)

    def _items_by_label(self):
        return {it["label"]: it for it in self.selected() if it["kind"] == "screen"}

    def _generate_flows(self):
        flows = sgif.build_flows(self.selected(), all_items=self.items,
                                 max_flows=self._num(self.flow_max, 12, lo=1),
                                 target_steps=self._num(self.flow_steps, 5, lo=2))
        self.flows = [{"on": tk.BooleanVar(value=True), "steps": [it["label"] for it in f]} for f in flows]
        self._flows_sig = tuple(it["label"] for it in self.selected())

    def _regen_flows(self):
        self._generate_flows()
        self._render_flows()

    def _new_flow(self):
        by = self._items_by_label()
        if not by:
            return
        self.flows.append({"on": tk.BooleanVar(value=True), "steps": [next(iter(by))]})
        self._render_flows()

    def _remove_flow(self, fi):
        del self.flows[fi]
        self._render_flows()

    def _remove_step(self, fi, si):
        del self.flows[fi]["steps"][si]
        self._refresh_flow(fi)

    def _move_step(self, fi, si, delta):
        steps = self.flows[fi]["steps"]
        j = si + delta
        if 0 <= j < len(steps):
            steps[si], steps[j] = steps[j], steps[si]
            self._refresh_flow(fi)

    def _add_step(self, fi, label):
        self.flows[fi]["steps"].append(label)
        self._refresh_flow(fi)

    def _refresh_flow(self, fi):
        """Rebuild just one flow card (cheap) instead of the whole list."""
        self._fill_flow_card(self._flow_cards[fi], fi)
        self._update_flow_count()

    def _add_page_menu(self, fi, button):
        present = set(self.flows[fi]["steps"])
        menu = tk.Menu(self, tearoff=0, bg=FIELD, fg=FG, activebackground=ORANGE, activeforeground=ON_ORANGE,
                       bd=0, relief="flat")
        n = 0
        for i, (label, _it) in enumerate(self._items_by_label().items(), 1):
            if label in present:
                continue
            menu.add_command(label=f"{i}.  {short_label(label, 40)}", columnbreak=(n > 0 and n % 18 == 0),
                             command=lambda lb=label: self._add_step(fi, lb))
            n += 1
        if n == 0:
            menu.add_command(label="Every selected page is already in this flow", state="disabled")
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        menu.grab_release()

    def _update_flow_count(self):
        by = self._items_by_label()
        usable = sum(1 for fl in self.flows if fl["on"].get() and sum(1 for lb in fl["steps"] if lb in by) >= 2)
        self.flow_count.configure(text=f"{usable} of {len(self.flows)} flows will be created")

    def _render_flows(self):
        for w in self.flow_list.winfo_children():
            w.destroy()
        self._flow_cards = []
        if not self.flows:
            ctk.CTkLabel(self.flow_list, justify="left", font=self.f_body, text_color=MUTED,
                         text="No flows could be generated from this selection.\nFlows follow the links between "
                              "pages. You can still build your own with “+ New flow”."
                         ).grid(row=0, column=0, sticky="w", padx=8, pady=20)
        for fi in range(len(self.flows)):
            card = self._card(self.flow_list)
            card.grid(row=fi, column=0, sticky="ew", padx=(0, 10), pady=(0, 12))
            card.grid_columnconfigure(0, weight=1)
            self._flow_cards.append(card)
            self._fill_flow_card(card, fi)
        self._update_flow_count()

    def _tile_scale(self):
        try:
            return float(self._get_window_scaling())
        except Exception:
            return 1.0

    def _tiles_per_row(self):
        width = self.flow_list.winfo_width()
        if width < 300:
            return 4
        return max(2, min(8, int((width - 100) // (196 * self._tile_scale()))))

    def _tile_photo(self, item, size):
        key = (item["label"], size)
        if key not in self._tile_photos:
            buf = io.BytesIO()
            item["thumb"].resize(size, Image.LANCZOS).save(buf, "PNG")
            self._tile_photos[key] = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
        return self._tile_photos[key]

    def _fill_flow_card(self, card, fi):
        """(Re)build the contents of one flow card. Page tiles use light native Tk widgets and wrap onto
        several rows: no nested scroll areas, and far fewer canvas-based widgets to redraw while scrolling."""
        for w in card.winfo_children():
            w.destroy()
        fl = self.flows[fi]
        by = self._items_by_label()
        fl["steps"] = [lb for lb in fl["steps"] if lb in by]       # pages that are no longer selected disappear
        steps = [by[lb] for lb in fl["steps"]]
        names = sgif.crumbs(steps)
        ok = len(steps) >= 2

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkCheckBox(head, text=f"Flow {fi + 1}", variable=fl["on"], command=self._update_flow_count,
                        font=self.f_bold, text_color=FG, fg_color=ORANGE, hover_color=ORANGE_H, border_color=MUTED,
                        checkmark_color=ON_ORANGE, corner_radius=6, checkbox_width=22, checkbox_height=22
                        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text=f"{len(steps)} steps" if ok else "needs at least 2 steps", font=self.f_small,
                     text_color=MUTED if ok else DANGER).grid(row=0, column=1, sticky="w", padx=12)
        add = self._ghost(head, "+ Add page", None, width=100, height=30)
        add.configure(command=lambda b=add, i=fi: self._add_page_menu(i, b))
        add.grid(row=0, column=2, padx=(0, 8))
        self._ghost(head, "Remove flow", lambda i=fi: self._remove_flow(i), width=110, height=30).grid(row=0, column=3)
        ctk.CTkLabel(card, text=sgif.SEP.join(names) if steps else "(empty)", font=self.f_small, text_color=MUTED,
                     anchor="w", justify="left", wraplength=700).grid(row=1, column=0, sticky="w", padx=16, pady=(4, 6))

        strip = tk.Frame(card, bg=PANEL, highlightthickness=0, bd=0)
        strip.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 12))
        per_row, sc = self._tiles_per_row(), self._tile_scale()
        size = (int(150 * sc), int(94 * sc))
        small = ("Segoe UI", 9)
        for si, it in enumerate(steps):
            row, col = divmod(si, per_row)
            tile = tk.Frame(strip, bg=PANEL_H, highlightthickness=1, highlightbackground=BORDER, bd=0)
            tile.grid(row=row, column=col * 2, padx=2, pady=4)
            if it.get("thumb") is not None:
                pic = tk.Label(tile, image=self._tile_photo(it, size), bg=PANEL_H, bd=0)
            else:
                pic = tk.Label(tile, text="(no preview)", fg=MUTED, bg=PANEL_H, width=18, height=5)
            pic.grid(row=0, column=0, columnspan=3, padx=8, pady=(8, 4))
            title = f"{si + 1}. {names[si]}"
            tk.Label(tile, text=title if len(title) <= 22 else title[:21] + "…", fg=FG, bg=PANEL_H,
                     font=small).grid(row=1, column=0, columnspan=3, padx=8)
            for c, (text, cmd, enabled) in enumerate((
                    ("◀", lambda i=fi, k=si: self._move_step(i, k, -1), si > 0),
                    ("✕", lambda i=fi, k=si: self._remove_step(i, k), True),
                    ("▶", lambda i=fi, k=si: self._move_step(i, k, 1), si < len(steps) - 1))):
                tk.Button(tile, text=text, command=cmd, width=3, bd=0, relief="flat", font=small, bg=FIELD,
                          fg=FG, activebackground=BORDER, activeforeground=FG, disabledforeground=FAINT_TEXT,
                          highlightthickness=0, cursor="hand2" if enabled else "arrow",
                          state="normal" if enabled else "disabled").grid(row=2, column=c, padx=3, pady=(4, 8))
            if si < len(steps) - 1:
                tk.Label(strip, text="›", fg=ORANGE, bg=PANEL, font=("Segoe UI", 20, "bold")
                         ).grid(row=row, column=col * 2 + 1, padx=4)

    def flows_next(self):
        self._prepare_save_page()
        self.go(4)

    # ───────────── page 4: save ─────────────
    def _switch(self, parent, text, var, command):
        return ctk.CTkSwitch(parent, text=text, variable=var, onvalue=True, offvalue=False, command=command,
                             progress_color=ORANGE, button_color=FG, button_hover_color=FG, fg_color=FIELD,
                             text_color=FG, font=self.f_body)

    def _segmented(self, parent, values, var, command):
        return ctk.CTkSegmentedButton(parent, values=values, variable=var, command=lambda _v: command(),
                                      selected_color=ORANGE, selected_hover_color=ORANGE_H, unselected_color=FIELD,
                                      unselected_hover_color=PANEL, fg_color=FIELD, text_color=FG, font=self.f_body,
                                      height=34, corner_radius=10)

    def _build_save_page(self):
        page = self.pages[4]
        self._header(page, "Save your results", "Choose what to save and where.")
        body = ctk.CTkScrollableFrame(page, fg_color="transparent", scrollbar_button_color=BORDER,
                                      scrollbar_button_hover_color=ORANGE)
        body.grid(row=1, column=0, sticky="nsew", padx=(36, 24))
        body.grid_columnconfigure(0, weight=1)

        f = self.save_form = self._card(body)
        f.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        f.grid_columnconfigure(0, weight=1)
        self.save_summary = ctk.CTkLabel(f, text="", font=self.f_bold_lg, text_color=FG, anchor="w")
        self.save_summary.grid(row=0, column=0, sticky="w", padx=24, pady=(22, 0))

        ctk.CTkLabel(f, text="WHAT TO SAVE", font=self.f_section, text_color=ORANGE).grid(
            row=1, column=0, sticky="w", padx=24, pady=(16, 0))
        opts = ctk.CTkFrame(f, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="w", padx=24, pady=(10, 4))
        self._switch(opts, "Screenshots (PNG)", self.save_png, self._refresh_save_ui).grid(row=0, column=0, padx=(0, 32))
        self._switch(opts, "GIF flows", self.make_gif, self._on_gif_toggle).grid(row=0, column=1)

        g = self.gif_frame = ctk.CTkFrame(f, fg_color=PANEL_H, corner_radius=12)
        g.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 4))
        g.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(g, fg_color="transparent")
        top.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(top, text="Style", font=self.f_small, text_color=MUTED).grid(row=0, column=0, padx=(0, 10))
        self.gif_style_seg = self._segmented(top, ["Slideshow", "Live recording"], self.gif_style, self._on_style)
        self.gif_style_seg.grid(row=0, column=1)
        self.style_note = ctk.CTkLabel(g, text="", justify="left", anchor="w", wraplength=620, font=self.f_small,
                                       text_color=MUTED)
        self.style_note.grid(row=1, column=0, sticky="w", padx=16, pady=(2, 8))
        self.opt_slide = ctk.CTkFrame(g, fg_color="transparent")
        self.opt_slide.grid(row=2, column=0, sticky="w", padx=16)
        ctk.CTkLabel(self.opt_slide, text="Seconds per step", font=self.f_small, text_color=MUTED).grid(
            row=0, column=0, padx=(0, 8))
        self._entry(self.opt_slide, self.gif_seconds, width=80, height=34).grid(row=0, column=1)
        self.opt_live = ctk.CTkFrame(g, fg_color="transparent")
        self.opt_live.grid(row=2, column=0, sticky="w", padx=16)
        ctk.CTkLabel(self.opt_live, text="Quality", font=self.f_small, text_color=MUTED).grid(row=0, column=0, padx=(0, 10))
        self._segmented(self.opt_live, ["Compact", "Standard", "High"], self.gif_quality,
                        self._refresh_save_ui).grid(row=0, column=1)
        self._switch(g, "App icon watermark", self.gif_wm, None).grid(row=3, column=0, sticky="w", padx=16, pady=(12, 0))
        self.flow_preview = ctk.CTkLabel(g, text="", justify="left", anchor="w", wraplength=620, font=self.f_small,
                                         text_color=FG)
        self.flow_preview.grid(row=4, column=0, sticky="w", padx=16, pady=(10, 14))
        g.grid_remove()

        ctk.CTkLabel(f, text="SAVE LOCATION", font=self.f_section, text_color=ORANGE).grid(
            row=4, column=0, sticky="w", padx=24, pady=(16, 0))
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=24, pady=(8, 6))
        row.grid_columnconfigure(0, weight=1)
        self._entry(row, self.save_dir, "Choose a folder").grid(row=0, column=0, sticky="ew")
        self._ghost(row, "Browse…", self.pick_save, width=100).grid(row=0, column=1, padx=(8, 0))
        self.save_err = ctk.CTkLabel(f, text="", font=self.f_body, text_color=DANGER, anchor="w", justify="left",
                                     wraplength=640)
        self.save_err.grid(row=6, column=0, sticky="w", padx=24)
        self.save_status = ctk.CTkLabel(f, text="", font=self.f_small, text_color=MUTED, anchor="w")
        self.save_status.grid(row=7, column=0, sticky="w", padx=24)
        ctk.CTkFrame(f, height=16, fg_color="transparent").grid(row=8, column=0)   # bottom padding

        self.save_done = self._card(body)
        self.save_done.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.save_done.grid_columnconfigure(0, weight=1)
        badge, _ = self._circle(self.save_done, "✓", size=64, fg=OK, text_color="#052E16",
                                font=ctk.CTkFont(size=32, weight="bold"))
        badge.grid(row=0, column=0, pady=(30, 12))
        self.done_title = ctk.CTkLabel(self.save_done, text="", font=self.f_title, text_color=FG)
        self.done_title.grid(row=1, column=0)
        self.done_path = ctk.CTkLabel(self.save_done, text="", font=self.f_body, text_color=MUTED, wraplength=640)
        self.done_path.grid(row=2, column=0, pady=(6, 18))
        btns = ctk.CTkFrame(self.save_done, fg_color="transparent")
        btns.grid(row=3, column=0, pady=(0, 30))
        self._primary(btns, "Open folder", lambda: open_path(self.saved_dir), width=150).pack(side="left")
        self._ghost(btns, "Capture something else", self.reset_all, width=200).pack(side="left", padx=10)
        self.save_done.grid_remove()
        # the main action lives in the footer so it is always visible, even with the GIF panel open
        _back, self.save_btn = self._footer(page, lambda: self.go(3), "Save", self.do_save)
        self.save_btn.configure(width=250)

    def _prepare_save_page(self):
        self.saved_dir = None
        self.save_form.grid()
        self.save_done.grid_remove()
        n = len(self.selected())
        self.save_summary.configure(text=f"{n} {'screenshot' if n == 1 else 'screenshots'} selected")
        self.save_err.configure(text="")
        self.save_status.configure(text="")
        self.save_dir.set(self.settings["save_dir"])
        web = bool(self.job) and self.job[0] == "web"
        if not web:
            self.gif_style.set("Slideshow")
        self.gif_style_seg.configure(state="normal" if web else "disabled")
        self.save_btn.grid()
        self.save_btn.configure(state="normal")
        self._on_style()

    def pick_save(self):
        f = filedialog.askdirectory()
        if f:
            self.save_dir.set(f)

    def _active_flows(self):
        """The flows that will become GIFs: switched on and with at least two pages."""
        by = self._items_by_label()
        result = []
        for fl in self.flows:
            steps = [by[lb] for lb in fl["steps"] if lb in by]
            if fl["on"].get() and len(steps) >= 2:
                result.append(steps)
        return result

    def _on_gif_toggle(self):
        if self.make_gif.get():
            self.gif_frame.grid()
        else:
            self.gif_frame.grid_remove()
        self._on_style()

    def _on_style(self):
        live = self.gif_style.get() == "Live recording"
        if live:
            self.opt_slide.grid_remove()
            self.opt_live.grid()
            self.style_note.configure(text="Replays every flow in a real browser — scrolling, moving the mouse and "
                                           "clicking the link to the next page — and records it moment by moment. "
                                           "Smoother, but slower and the files are bigger.")
        else:
            self.opt_live.grid_remove()
            self.opt_slide.grid()
            note = "One frame per page — quick to create and small."
            if not (bool(self.job) and self.job[0] == "web"):
                note += " Live recording is available for websites only."
            self.style_note.configure(text=note)
        self._refresh_save_ui()

    def _refresh_save_ui(self, *_):
        """Update the flow preview and the label of the Save button."""
        if not hasattr(self, "save_btn"):
            return
        n = len(self.selected())
        png, gif = self.save_png.get(), self.make_gif.get()
        flows = self._active_flows() if gif else []
        if gif:
            lines = sgif.describe_flows(flows)
            if flows:
                text = f"{len(flows)} GIF{'s' if len(flows) != 1 else ''} will be created in a “flows” folder:\n"
                text += "\n".join(f"{i}.   {line}" for i, line in enumerate(lines[:6], 1))
                if len(lines) > 6:
                    text += f"\n… and {len(lines) - 6} more"
            else:
                text = "No flows are switched on. Go back to Flows to enable or create some."
            self.flow_preview.configure(text=text)
        if png and gif:
            label = "Save screenshots + GIFs"
        elif gif:
            label = f"Create {len(flows)} GIF{'s' if len(flows) != 1 else ''}"
        elif png:
            label = f"Save {n} screenshot{'s' if n != 1 else ''}"
        else:
            label = "Save"
        if str(self.save_btn.cget("state")) == "normal":
            self.save_btn.configure(text=label)

    def do_save(self):
        raw = self.save_dir.get().strip()
        png, gif = self.save_png.get(), self.make_gif.get()
        if not png and not gif:
            self.save_err.configure(text="Turn on Screenshots (PNG), GIF flows, or both.")
            return
        if not raw:
            self.save_err.configure(text="Choose a folder first.")
            return
        keys = ("path", "label", "parent", "title", "kind", "links")
        flows = [[{k: it[k] for k in keys} for it in f] for f in self._active_flows()] if gif else []
        if gif and not flows and not png:
            self.save_err.configure(text="No flows are switched on. Go back to Flows to enable or create some.")
            return
        chosen = [{k: it[k] for k in keys} for it in self.selected()]
        self.save_err.configure(text="")
        self.save_status.configure(text="Saving…")
        self.save_btn.configure(state="disabled", text="Saving…")
        threading.Thread(
            target=self._save_worker,
            args=(Path(raw).expanduser(), chosen, flows, png, gif, self.gif_style.get(),
                  self._num(self.gif_seconds, 1.6, float, lo=0.2), self.gif_quality.get().lower(),
                  bool(self.gif_wm.get()), self.scan_ns),
            daemon=True).start()

    def _save_worker(self, dest, chosen, flows, png, gif, style, seconds, quality, watermark, scan_ns):
        try:
            dest.mkdir(parents=True, exist_ok=True)
            copied = 0
            if png:
                for it in chosen:
                    target = dest / it["path"].name
                    k = 1
                    while target.exists():
                        target = dest / f"{it['path'].stem} ({k}){it['path'].suffix}"
                        k += 1
                    shutil.copy2(it["path"], target)
                    copied += 1
            made = []
            if gif and flows:
                def progress(n, total, crumbs):
                    self.q.put(("save_progress", f"Creating GIF {n} of {total}…"))
                if style == "Live recording":
                    sa.STOP.clear()
                    try:
                        made = slive.record_flows(flows, dest / "flows", scan_ns, quality, watermark, progress)
                    except sa.BrowserUnavailable as e:
                        self.q.put(("save_progress", f"No usable browser found ({e}). Downloading Chromium…"))
                        sa.install_chromium()
                        made = slive.record_flows(flows, dest / "flows", scan_ns, quality, watermark, progress)
                else:
                    made = sgif.render_flows(flows, dest / "flows", seconds, watermark, progress)
            self.q.put(("save_done", copied, len(made), str(dest)))
        except Exception as e:
            self.q.put(("save_error", str(e)))

    def _save_done(self, copied, gifs, dest):
        self.settings["save_dir"] = dest
        store_settings(self.settings)
        self.saved_dir = Path(dest)
        parts = []
        if copied:
            parts.append(f"{copied} screenshot{'s' if copied != 1 else ''}")
        if gifs:
            parts.append(f"{gifs} GIF flow{'s' if gifs != 1 else ''}")
        self.done_title.configure(text=("Saved " if copied else "Created ") + " and ".join(parts))
        self.done_path.configure(text=dest + ("   (GIFs are in the “flows” folder)" if gifs else ""))
        self.save_btn.configure(state="normal")
        self.save_btn.grid_remove()
        self.save_status.configure(text="")
        self.save_form.grid_remove()
        self.save_done.grid()
        if self.settings.get("open_after_save"):
            open_path(dest)

    # ───────────── lifecycle ─────────────
    def reset_all(self):
        if self.scanning:
            sa.STOP.set()
        self._cleanup_temp()
        for it in self.items:
            it["card"].destroy()
        self.items = []
        self.flows, self._flows_sig = [], None
        self.buf.clear()
        self.go(0)

    def _cleanup_temp(self):
        if self.temp:
            shutil.rmtree(self.temp, ignore_errors=True)
            self.temp = None

    def on_close(self):
        sa.STOP.set()
        self._cleanup_temp()
        self.destroy()

    def poll(self):
        try:
            for _ in range(25):
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "item":
                    self._add_card(*msg[1:])
                elif kind == "log":
                    text = msg[1]
                    line = text.strip().splitlines()[-1] if text.strip() else ""
                    if line and self.scanning:
                        self.status.configure(text=line[:110])
                    if self.details_visible:
                        self.details.configure(state="normal")
                        self.details.insert("end", text)
                        self.details.see("end")
                        self.details.configure(state="disabled")
                elif kind == "done":
                    self._scan_done(msg[1])
                elif kind == "save_progress":
                    self.save_status.configure(text=msg[1])
                elif kind == "save_done":
                    self._save_done(msg[1], msg[2], msg[3])
                elif kind == "save_error":
                    self.save_err.configure(text=f"Could not save: {msg[1]}")
                    self.save_status.configure(text="")
                    self.save_btn.configure(state="normal")
                    self._refresh_save_ui()
        except queue.Empty:
            pass
        self._maybe_reflow()
        self.after(80, self.poll)


if __name__ == "__main__":
    App().mainloop()
