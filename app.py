"""Res & Vibrance Switch - desktop app (CustomTkinter UI, tray icon, global hotkeys)."""

import base64
import ctypes
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
import winreg
import zlib
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

import detect
import presets
import resvib
import updater
import winevents
from version import UPDATE_REPO, __version__

APP_NAME = "Res & Vibrance Switch"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ResVibranceSwitch"
MUTEX_NAME = "ResVibranceSwitchMutex"
SHOW_EVENT_NAME = "ResVibranceSwitchShowEvent"
SHARE_PREFIX = "RVS1:"
SHAREABLE_KEYS = ("width", "height", "refresh", "vibrance", "brightness", "contrast", "gamma", "alt_tab")

# Colour tokens as (light, dark) pairs; CustomTkinter picks one based on the theme.
C = {
    "bg": ("#eef0f5", "#0c0d12"),
    "card": ("#ffffff", "#15171e"),
    "border": ("#e2e5ee", "#232633"),
    "tile": ("#f4f5fa", "#1c1f29"),
    "row": ("#f7f8fc", "#1a1d26"),
    "input": ("#f0f1f7", "#1f2230"),
    "input_btn": ("#e1e4ef", "#2a2e3e"),
    "switch_off": ("#c3c7d4", "#2f3344"),
    "chip": ("#eceef5", "#232634"),
    "chip_hover": ("#dfe2ed", "#2e3245"),
    "text": ("#151821", "#eef0f7"),
    "muted": ("#6a7081", "#8a90a3"),
    "accent": ("#6a4cff", "#7c5cff"),
    "accent_hover": ("#5937f2", "#6b49f7"),
    "accent_text": ("#5a3df0", "#a593ff"),
    "accent_soft": ("#ece8ff", "#241d45"),
    "green": ("#15803d", "#4ade80"),
    "green_soft": ("#dcfce7", "#122a1c"),
    "amber": ("#b45309", "#fbbf24"),
    "amber_soft": ("#fef3c7", "#2d2410"),
    "danger": ("#dc2626", "#f87171"),
    "danger_soft": ("#fee2e2", "#3a1619"),
}
GRADIENT = [(0x22, 0xD3, 0xEE), (0x7C, 0x5C, 0xFF), (0xEC, 0x48, 0x99), (0xF5, 0x9E, 0x0B)]
AVATAR_COLORS = ["#7c5cff", "#ef4467", "#0ea5e9", "#f59e0b", "#10b981", "#ec4899", "#6366f1", "#14b8a6"]
TRAY_DOTS = {"paused": "#f59e0b", "watching": "#22c55e", "game": "#a855f7"}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def gradient_color(t):
    """Colour at position t (0..1) along the vibrance gradient."""
    t = min(max(t, 0.0), 1.0) * (len(GRADIENT) - 1)
    i = min(int(t), len(GRADIENT) - 2)
    a, b, f = GRADIENT[i], GRADIENT[i + 1], t - i
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def make_icon_image(size=64):
    """A monitor with a vibrance-gradient screen - drawn so no image file needs shipping."""
    scale = 4  # draw large, then downsample for smooth edges
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big / 64
    dark = (24, 26, 34)
    d.rounded_rectangle([3 * s, 7 * s, 61 * s, 47 * s], radius=8 * s, fill=dark)
    left, top, right, bottom = int(8 * s), int(12 * s), int(56 * s), int(42 * s)
    screen = Image.new("RGBA", (right - left, bottom - top))
    sd = ImageDraw.Draw(screen)
    for x in range(right - left):
        sd.line([(x, 0), (x, bottom - top)], fill=gradient_color(x / max(1, right - left - 1)))
    mask = Image.new("L", screen.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, *screen.size], radius=int(4 * s), fill=255)
    img.paste(screen, (left, top), mask)
    d.rectangle([28 * s, 47 * s, 36 * s, 54 * s], fill=dark)
    d.rounded_rectangle([17 * s, 53 * s, 47 * s, 59 * s], radius=3 * s, fill=dark)
    return img.resize((size, size), Image.LANCZOS)


def tray_image(state):
    """App icon with a status dot: green watching, amber paused, purple in a game."""
    img = make_icon_image(64)
    d = ImageDraw.Draw(img)
    d.ellipse([36, 36, 63, 63], fill=(12, 13, 18, 255))
    d.ellipse([41, 41, 58, 58], fill=TRAY_DOTS[state])
    return img


def icon_file():
    path = Path(tempfile.gettempdir()) / "resvibranceswitch.ico"
    make_icon_image(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return str(path)


def already_running():
    """Single-instance guard via a named mutex (kept alive for the process lifetime)."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def signal_running_instance():
    """Ask the already-running copy to show its window. Returns True if it was reached."""
    handle = kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)  # EVENT_MODIFY_STATE
    if not handle:
        return False
    kernel32.SetEvent(handle)
    kernel32.CloseHandle(handle)
    return True


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}" --minimized'


def get_startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except FileNotFoundError:
        return False


def set_startup_enabled(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def windowed_exes():
    """Exe names of processes that currently have a visible, titled window (i.e. open apps)."""
    user32 = ctypes.windll.user32
    procs = resvib.running_processes()
    pids = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(collect, 0)
    skip = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
            "python.exe", "pythonw.exe", Path(sys.executable).name.lower()}
    return sorted({procs[p] for p in pids if p in procs} - skip)


def mode_label(width, height, refresh):
    return f"{width} \u00d7 {height}   \u00b7   {refresh} Hz"


def game_name(exe):
    return presets.NAME_BY_EXE.get(exe.lower(), Path(exe).stem)


def initials(name):
    words = re.findall(r"[A-Za-z0-9]+", name) or ["?"]
    return (words[0][0] + (words[1][0] if len(words) > 1 else words[0][1:2])).upper()


def avatar_color(name):
    return AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]


def shorten(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def shareable(games):
    return {exe: {k: p[k] for k in SHAREABLE_KEYS if k in p} for exe, p in games.items()}


def encode_share_code(games):
    payload = json.dumps(shareable(games), separators=(",", ":")).encode()
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode()


def validate_games(data):
    """Keep only well-formed game profiles from imported data."""
    games = {}
    for exe, p in (data or {}).items():
        if not (isinstance(exe, str) and exe.lower().endswith(".exe") and isinstance(p, dict)):
            continue
        try:
            profile = {"width": int(p["width"]), "height": int(p["height"])}
        except (KeyError, TypeError, ValueError):
            continue
        for key in SHAREABLE_KEYS[2:]:
            if key in p:
                profile[key] = p[key]
        games[exe.lower()] = profile
    return games


def decode_share_code(code):
    code = "".join(code.split())
    if not code.startswith(SHARE_PREFIX):
        raise ValueError("That doesn't look like a share code (it should start with RVS1:)")
    try:
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(code[len(SHARE_PREFIX):])))
    except Exception as e:
        raise ValueError("The share code is damaged or incomplete") from e
    return validate_games(data)


_fonts = {}
_families = {}


def font(size, weight="normal", display=False, mono=False):
    if not _families:
        available = set(tkfont.families())
        _families["text"] = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in available), "Arial")
        _families["display"] = next((f for f in ("Segoe UI Variable Display", "Segoe UI") if f in available),
                                    "Arial")
        _families["mono"] = next((f for f in ("Cascadia Mono", "Consolas") if f in available), "Courier New")
    family = _families["mono" if mono else "display" if display else "text"]
    key = (family, size, weight)
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family=family, size=size, weight=weight)
    return _fonts[key]


# --------------------------------------------------------------------------
# Styled widget factories
# --------------------------------------------------------------------------

def card(master, **kw):
    kw.setdefault("corner_radius", 16)
    return ctk.CTkFrame(master, fg_color=C["card"], border_width=1, border_color=C["border"], **kw)


def primary_button(master, text, command, **kw):
    kw.setdefault("height", 38)
    return ctk.CTkButton(master, text=text, command=command, fg_color=C["accent"],
                         hover_color=C["accent_hover"], text_color="#ffffff", corner_radius=10,
                         font=font(13, "bold"), **kw)


def secondary_button(master, text, command, **kw):
    kw.setdefault("height", 38)
    return ctk.CTkButton(master, text=text, command=command, fg_color=C["chip"],
                         hover_color=C["chip_hover"], text_color=C["text"], corner_radius=10,
                         font=font(13, "bold"), **kw)


def small_button(master, text, command, danger=False, accent=False, width=54):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=30, corner_radius=8,
                         fg_color="transparent", font=font(12, "bold" if accent else "normal"),
                         hover_color=C["danger_soft"] if danger else C["accent_soft"] if accent else C["chip_hover"],
                         text_color=C["danger"] if danger else C["accent_text"] if accent else C["text"])


def badge(master, text, fg, color, **kw):
    kw.setdefault("height", 22)
    return ctk.CTkLabel(master, text=text, fg_color=fg, text_color=color, corner_radius=8,
                        font=font(11, "bold"), **kw)


def caption(master, text):
    return ctk.CTkLabel(master, text=text, font=font(13), text_color=C["muted"], anchor="w")


def note(master, text, wraplength=440, color=None):
    return ctk.CTkLabel(master, text=text, font=font(12), text_color=color or C["muted"], anchor="w",
                        justify="left", wraplength=wraplength)


def option_menu(master, variable, values, command=None):
    return ctk.CTkOptionMenu(master, variable=variable, values=values, command=command, height=38,
                             corner_radius=10, fg_color=C["input"], button_color=C["input_btn"],
                             button_hover_color=C["chip_hover"], text_color=C["text"],
                             dropdown_fg_color=C["card"], dropdown_hover_color=C["chip"],
                             dropdown_text_color=C["text"], font=font(13), dropdown_font=font(12),
                             dynamic_resizing=False)


def switch(master, text, variable, command=None):
    return ctk.CTkSwitch(master, text=text, variable=variable, command=command, font=font(13),
                         text_color=C["text"], progress_color=C["accent"], button_color="#ffffff",
                         button_hover_color="#f0f0f0", fg_color=C["switch_off"], switch_width=40,
                         switch_height=20)


def entry(master, placeholder=""):
    return ctk.CTkEntry(master, placeholder_text=placeholder, height=38, corner_radius=10, fg_color=C["input"],
                        border_color=C["border"], text_color=C["text"], font=font(13))


class ResolutionControl(ctk.CTkFrame):
    def __init__(self, master, app, display, profile, on_change=None):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.display = display
        caption(self, "Resolution").pack(fill="x")
        self.var = tk.StringVar(value=app.mode_string(profile, display))
        self.menu = option_menu(self, self.var, list(app.modes_for(display)),
                                command=(lambda _: on_change()) if on_change else None)
        self.menu.pack(fill="x", pady=(6, 0))

    def set_display(self, display):
        self.display = display
        labels = list(self.app.modes_for(display))
        self.menu.configure(values=labels)
        if self.var.get() not in labels:
            self.var.set(labels[0])

    def get(self):
        return self.app.modes_for(self.display)[self.var.get()]


class LabeledSlider(ctk.CTkFrame):
    def __init__(self, master, label, value, start, end, steps, fmt, on_change=None, hints=("", ""),
                 enabled=True, decimals=0):
        super().__init__(master, fg_color="transparent")
        self.on_change = on_change
        self.fmt = fmt
        self.decimals = decimals
        self._job = None
        self.value = value
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        caption(top, label).pack(side="left")
        self.value_label = ctk.CTkLabel(top, text="", font=font(13, "bold"), text_color=C["accent_text"])
        self.value_label.pack(side="right")
        self.slider = ctk.CTkSlider(self, from_=start, to=end, number_of_steps=steps, command=self._changed,
                                    height=18, button_color=C["accent"], button_hover_color=C["accent_hover"],
                                    progress_color=C["accent"], fg_color=C["input_btn"])
        self.slider.set(value)
        self.slider.pack(fill="x", pady=(8, 2))
        if any(hints):
            scale = ctk.CTkFrame(self, fg_color="transparent")
            scale.pack(fill="x")
            for side, text in zip(("left", "right"), hints):
                ctk.CTkLabel(scale, text=text, font=font(11), text_color=C["muted"]).pack(side=side)
        self.set_enabled(enabled)
        self._render()

    def set_enabled(self, enabled):
        if enabled:
            self.slider.configure(state="normal", button_color=C["accent"], progress_color=C["accent"])
        else:
            self.slider.configure(state="disabled", button_color=C["switch_off"], progress_color=C["switch_off"])

    def _changed(self, v):
        self.value = round(v, self.decimals) if self.decimals else int(round(v))
        self._render()
        if self.on_change:
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(350, self.on_change)  # act once the slider settles

    def _render(self):
        self.value_label.configure(text=self.fmt(self.value))

    def set(self, value):
        self.value = value
        self.slider.set(value)
        self._render()

    def get(self):
        return self.value


def vibrance_slider(master, value, enabled=True, on_change=None):
    return LabeledSlider(master, "Digital vibrance", value if value is not None else 50, 50, 100, 50,
                         lambda v: f"{v}%" if enabled else "Not supported", on_change,
                         ("50%  default", "100%  max"), enabled)


class HotkeyButton(ctk.CTkFrame):
    """Click, then press a key combination to set a global hotkey."""

    MODIFIER_KEYSYMS = {"shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r", "win_l", "win_r",
                        "super_l", "super_r", "caps_lock", "num_lock", "meta_l", "meta_r"}

    def __init__(self, master, value="", on_change=None):
        super().__init__(master, fg_color="transparent")
        self.value = value or ""
        self.on_change = on_change
        self._binding = None
        self.button = secondary_button(self, "", self.start_capture, height=34)
        self.button.pack(side="left", fill="x", expand=True)
        small_button(self, "Clear", self.clear, width=56).pack(side="left", padx=(6, 0))
        self._render()

    def _render(self, text=None):
        self.button.configure(text=text or (winevents.format_hotkey(self.value) if self.value else "Not set"),
                              fg_color=C["accent_soft"] if text else C["chip"],
                              text_color=C["accent_text"] if text else C["text"])

    def start_capture(self):
        self._render("Press a key combination\u2026")
        top = self.winfo_toplevel()
        self._binding = top.bind("<KeyPress>", self._on_key, add="+")
        top.focus_force()

    def _stop_capture(self):
        if self._binding:
            self.winfo_toplevel().unbind("<KeyPress>", self._binding)
            self._binding = None
        self._render()

    def _on_key(self, event):
        key = event.keysym.lower()
        if key in self.MODIFIER_KEYSYMS:
            return "break"
        mods = [name for name, mask in (("ctrl", 0x4), ("alt", 0x20000), ("shift", 0x1)) if event.state & mask]
        if key not in winevents.KEYS:
            self._render("That key isn't supported - try another")
            return "break"
        if not mods and not re.fullmatch(r"f\d+", key):
            self._render("Add Ctrl, Alt or Shift to that key")
            return "break"
        self.value = "+".join(mods + [key])
        self._stop_capture()
        if self.on_change:
            self.on_change(self.value)
        return "break"

    def clear(self):
        self.value = ""
        self._stop_capture()
        if self.on_change:
            self.on_change("")

    def get(self):
        return self.value


class GameChecklist(ctk.CTkScrollableFrame):
    """Searchable checklist of preset games with INSTALLED / RUNNING badges."""

    def __init__(self, master, app, installed, preselect_installed=False, height=240, on_change=None):
        super().__init__(master, height=height, fg_color=C["card"], corner_radius=12, border_width=1,
                         border_color=C["border"], scrollbar_button_color=C["chip"],
                         scrollbar_button_hover_color=C["chip_hover"])
        self.on_change = on_change or (lambda: None)
        configured = set(app.config["games"])
        running = resvib.running_exes()
        games = [(name, g) for name, g in presets.GAMES.items()
                 if not all(e.lower() in configured for e in g["exes"])]
        games.sort(key=lambda item: (item[0] not in installed, item[0].lower()))
        self.rows = []
        for name, game in games:
            row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=8)
            var = tk.BooleanVar(value=preselect_installed and name in installed)
            box = ctk.CTkCheckBox(row, text=name, variable=var, command=self.on_change, font=font(13),
                                  text_color=C["text"], fg_color=C["accent"], hover_color=C["accent_hover"],
                                  border_color=C["muted"], checkbox_width=20, checkbox_height=20,
                                  corner_radius=6, border_width=2)
            box.pack(side="left", padx=10, pady=7)
            if any(e.lower() in running for e in game["exes"]):
                badge(row, "  RUNNING  ", C["green_soft"], C["green"]).pack(side="right", padx=(0, 10))
            if name in installed:
                badge(row, f"  {installed[name]['source'].upper()}  ", C["accent_soft"], C["accent_text"]).pack(
                    side="right", padx=(0, 6))
            row.bind("<Button-1>", lambda e, b=box: b.toggle())
            self.rows.append((name, row, var))
        self.empty = ctk.CTkLabel(self, text="", font=font(13), text_color=C["muted"])
        self.filter("")

    def filter(self, term):
        term = term.strip().lower()
        for _name, row, _var in self.rows:
            row.pack_forget()
        shown = 0
        for name, row, _var in self.rows:
            if term in name.lower():
                row.pack(fill="x", padx=4, pady=1)
                shown += 1
        self.empty.pack_forget()
        if not shown:
            self.empty.configure(text="All listed games are already added." if not self.rows
                                 else "No games match your search.")
            self.empty.pack(pady=30)

    def selected(self):
        return [name for name, _row, var in self.rows if var.get()]

    def select(self, names):
        for name, _row, var in self.rows:
            if name in names:
                var.set(True)
        self.on_change()


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------

class Dialog(ctk.CTkToplevel):
    def __init__(self, app, title, width):
        super().__init__(app.root, fg_color=C["bg"])
        self.app = app
        self.result = None
        self.width = width
        self.title(title)
        self.resizable(False, False)
        if app.root.winfo_viewable():
            self.transient(app.root)
        # CustomTkinter sets its own icon shortly after creation; set ours after it.
        self.after(250, lambda: self.iconbitmap(app.icon_path))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=26, pady=22)
        # Fixes the width; the height follows the content.
        ctk.CTkFrame(self.body, fg_color="transparent", width=width - 52, height=0).pack()

    def close(self):
        self.destroy()

    def header(self, title, subtitle=""):
        ctk.CTkLabel(self.body, text=title, font=font(20, "bold", display=True),
                     text_color=C["text"], anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(self.body, text=subtitle, font=font(13), text_color=C["muted"], anchor="w",
                         justify="left", wraplength=self.width - 60).pack(fill="x", pady=(2, 16))

    def section(self, parent, title):
        ctk.CTkLabel(parent, text=title, font=font(14, "bold", display=True), text_color=C["text"],
                     anchor="w").pack(fill="x", pady=(16, 8))

    def footer(self, ok_text, ok_command, danger=False, cancel=True, cancel_text="Cancel", cancel_command=None):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(24, 0))
        ok = primary_button(row, ok_text, ok_command, width=130)
        if danger:
            ok.configure(fg_color=("#dc2626", "#dc2626"), hover_color=("#b91c1c", "#b91c1c"))
        ok.pack(side="right")
        if cancel:
            secondary_button(row, cancel_text, cancel_command or self.close, width=100).pack(side="right",
                                                                                             padx=(0, 8))
        self.bind("<Return>", lambda e: ok_command())
        return ok

    def show(self):
        self.update_idletasks()
        root = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        if root.winfo_viewable():
            x = root.winfo_rootx() + (root.winfo_width() - w) // 2
            y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        else:
            x, y = (self.winfo_screenwidth() - w) // 2, (self.winfo_screenheight() - h) // 3
        tk.Toplevel.wm_geometry(self, f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
        self.lift()
        self.after(80, self._grab)
        self.wait_window()
        return self.result

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            self.after(80, self._grab)


class ConfirmDialog(Dialog):
    def __init__(self, app, title, message, ok_text="OK", danger=False, cancel=True):
        super().__init__(app, title, 440)
        self.header(title, message)
        self.footer(ok_text, self.ok, danger=danger, cancel=cancel)

    def ok(self):
        self.result = True
        self.destroy()


class KeepChangesDialog(Dialog):
    """'Keep these settings?' with an automatic revert countdown - protects against unsupported modes."""

    def __init__(self, app, title, message, revert, seconds=15):
        super().__init__(app, title, 460)
        self.revert_fn = revert
        self.seconds = seconds
        self.remaining = seconds
        self.done = False
        self.header(title, message)
        self.count = ctk.CTkLabel(self.body, text="", font=font(13, "bold"), text_color=C["amber"], anchor="w")
        self.count.pack(fill="x")
        self.bar = ctk.CTkProgressBar(self.body, height=6, corner_radius=3, progress_color=C["amber"],
                                      fg_color=C["input_btn"])
        self.bar.pack(fill="x", pady=(8, 0))
        self.footer("Keep changes", self.keep, cancel_text="Revert", cancel_command=self.revert)
        self.result = False
        self._tick()

    def _tick(self):
        if self.done:
            return
        if self.remaining <= 0:
            self.revert()
            return
        self.count.configure(text=f"Reverting automatically in {self.remaining} s")
        self.bar.set(self.remaining / self.seconds)
        self.remaining -= 1
        self.after(1000, self._tick)

    def keep(self):
        self.done = True
        self.result = True
        self.destroy()

    def revert(self):
        if self.done:
            return
        self.done = True
        self.destroy()
        self.revert_fn()

    close = revert


class ProfileDialog(Dialog):
    def __init__(self, app, title, exes=None, profile=None):
        super().__init__(app, title, 560)
        self.exes = exes or []
        if profile:
            self.profile = dict(profile)
        else:  # new game: start from an existing game's display settings, never its launch/hotkey
            template = next(iter(app.config["games"].values()), None)
            if template:
                self.profile = {k: template[k] for k in ("width", "height", "refresh", "vibrance") if k in template}
            else:
                self.profile = {**dict(zip(("width", "height", "refresh"), resvib.get_mode(app.primary))),
                                "vibrance": 80}
        p = self.profile
        self.header(title, "Choose the game's process and how your display should look while it's running.")

        self.tabs = tabs = ctk.CTkTabview(self.body, height=470, corner_radius=12, fg_color=C["card"], border_width=1,
                              border_color=C["border"], segmented_button_fg_color=C["chip"],
                              segmented_button_selected_color=C["accent"],
                              segmented_button_selected_hover_color=C["accent_hover"],
                              segmented_button_unselected_color=C["chip"],
                              segmented_button_unselected_hover_color=C["chip_hover"],
                              text_color=C["text"])
        tabs.pack(fill="x")
        general = tabs.add("General")
        colour = tabs.add("Colour")
        launch = tabs.add("Launch & hotkey")
        for tab in (general, colour, launch):
            tab.configure(fg_color="transparent")

        # --- General
        g = ctk.CTkFrame(general, fg_color="transparent")
        g.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        caption(g, "Game process").pack(fill="x")
        self.entry = None
        if len(self.exes) > 1:
            ctk.CTkLabel(g, text="   " + ",  ".join(self.exes), fg_color=C["input"], corner_radius=10,
                         height=38, anchor="w", font=font(13), text_color=C["text"]).pack(fill="x", pady=(6, 0))
        else:
            row = ctk.CTkFrame(g, fg_color="transparent")
            row.pack(fill="x", pady=(6, 0))
            self.entry = entry(row, "e.g. cs2.exe")
            self.entry.pack(side="left", fill="x", expand=True)
            if self.exes:
                self.entry.insert(0, self.exes[0])
            secondary_button(row, "Browse\u2026", self.browse_exe, width=96).pack(side="left", padx=(8, 0))
            running = windowed_exes()
            if running:
                self.pick = tk.StringVar(value="Or pick an open app\u2026")
                option_menu(g, self.pick, running, command=self.picked).pack(fill="x", pady=(8, 0))

        self.displays = resvib.list_displays()
        self.display = resvib.resolve_display(p.get("display"))
        if len(self.displays) > 1:
            caption(g, "Monitor").pack(fill="x", pady=(14, 0))
            labels = {d.label: d.name for d in self.displays}
            self.display_names = labels
            current = next(d.label for d in self.displays if d.name == self.display)
            self.display_var = tk.StringVar(value=current)
            option_menu(g, self.display_var, list(labels), command=self.display_changed).pack(fill="x", pady=(6, 0))

        self.res = ResolutionControl(g, app, self.display, p)
        self.res.pack(fill="x", pady=(14, 0))
        self.vib_holder = ctk.CTkFrame(g, fg_color="transparent")
        self.vib_holder.pack(fill="x", pady=(14, 0))
        self.vib = None
        self.build_vibrance(p.get("vibrance", 80))
        self.alt_tab = tk.BooleanVar(value=p.get("alt_tab", True))
        switch(g, "Use desktop colours when I alt-tab out of the game", self.alt_tab).pack(anchor="w", pady=(16, 0))

        # --- Colour
        c = ctk.CTkFrame(colour, fg_color="transparent")
        c.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        note(c, "Fine-tune brightness, contrast and gamma for this game - handy for spotting enemies in dark "
                "corners. They switch back automatically on the desktop.", 440).pack(fill="x")
        self.colour_note = note(c, "", 440, C["amber"])
        self.colour_note.pack(fill="x", pady=(6, 0))
        self.brightness = LabeledSlider(c, "Brightness", p.get("brightness", 50), 0, 100, 100, lambda v: f"{v}",
                                        hints=("Darker", "Brighter"))
        self.brightness.pack(fill="x", pady=(14, 0))
        self.contrast = LabeledSlider(c, "Contrast", p.get("contrast", 50), 0, 100, 100, lambda v: f"{v}",
                                      hints=("Less", "More"))
        self.contrast.pack(fill="x", pady=(14, 0))
        self.gamma = LabeledSlider(c, "Gamma", p.get("gamma", 1.0), 0.5, 2.5, 40, lambda v: f"{v:.2f}",
                                   hints=("0.50", "2.50"), decimals=2)
        self.gamma.pack(fill="x", pady=(14, 0))
        secondary_button(c, "Reset to defaults", self.reset_colour, height=32, width=150).pack(anchor="w",
                                                                                            pady=(16, 0))
        self.update_colour_note()

        # --- Launch & hotkey
        lf = ctk.CTkFrame(launch, fg_color="transparent")
        lf.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        caption(lf, "Launch command").pack(fill="x")
        row = ctk.CTkFrame(lf, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.launch = entry(row, "steam://rungameid/730  or  C:\\Games\\game.exe")
        self.launch.pack(side="left", fill="x", expand=True)
        if p.get("launch"):
            self.launch.insert(0, p["launch"])
        secondary_button(row, "Browse\u2026", self.browse_launch, width=96).pack(side="left", padx=(8, 0))
        note(lf, "Adds a Play button that applies this profile first, then starts the game. Filled in "
                 "automatically for games found in Steam, Riot or Epic.", 440).pack(fill="x", pady=(6, 0))
        caption(lf, "Global hotkey").pack(fill="x", pady=(22, 0))
        self.hotkey = HotkeyButton(lf, p.get("hotkey", ""))
        self.hotkey.pack(fill="x", pady=(6, 0))
        note(lf, "Switches to this profile from anywhere, even in-game.", 440).pack(fill="x", pady=(6, 0))

        self.footer("Save", self.ok)

    def build_vibrance(self, value):
        for child in self.vib_holder.winfo_children():
            child.destroy()
        self.vib = vibrance_slider(self.vib_holder, value, self.app.color.supports_vibrance(self.display))
        self.vib.pack(fill="x")

    def display_changed(self, label):
        self.display = self.display_names[label]
        self.res.set_display(self.display)
        self.build_vibrance(self.vib.get())
        self.update_colour_note()

    def update_colour_note(self):
        reason = self.app.color.adjustment_unavailable_reason(self.display)
        self.colour_note.configure(text=reason or "")
        for slider in (self.brightness, self.contrast, self.gamma):
            slider.set_enabled(not reason)

    def reset_colour(self):
        for slider, value in ((self.brightness, 50), (self.contrast, 50), (self.gamma, 1.0)):
            slider.set(value)

    def picked(self, exe):
        self.entry.delete(0, "end")
        self.entry.insert(0, exe)

    def browse_exe(self):
        path = filedialog.askopenfilename(parent=self, title="Select the game's executable",
                                          filetypes=[("Programs", "*.exe")])
        if path:
            self.picked(Path(path).name)
            if not self.launch.get():
                self.launch.insert(0, str(Path(path)))

    def browse_launch(self):
        path = filedialog.askopenfilename(parent=self, title="Select the game or launcher",
                                          filetypes=[("Programs", "*.exe")])
        if path:
            self.launch.delete(0, "end")
            self.launch.insert(0, str(Path(path)))

    def ok(self):
        if self.entry is not None:
            exe = self.entry.get().strip().lower()
            if not exe:
                self.entry.configure(border_color=C["danger"])
                return
            if not exe.endswith(".exe"):
                exe += ".exe"
            exes = [exe]
        else:
            exes = self.exes
        p = dict(self.profile)
        p["width"], p["height"], p["refresh"] = self.res.get()
        if self.app.color.supports_vibrance(self.display):
            p["vibrance"] = self.vib.get()
        p["alt_tab"] = self.alt_tab.get()
        for key, slider in (("brightness", self.brightness), ("contrast", self.contrast), ("gamma", self.gamma)):
            p[key] = slider.get()
            if p[key] == resvib.NEUTRAL_COLOR[key]:
                p.pop(key)
        optional = {"display": None if self.display == self.app.primary else self.display,
                    "launch": self.launch.get().strip(), "hotkey": self.hotkey.get()}
        for key, value in optional.items():
            if value:
                p[key] = value
            else:
                p.pop(key, None)
        self.result = exes, p
        self.destroy()


class PresetDialog(Dialog):
    """Pick popular games from a searchable checklist and give them all the same settings."""

    def __init__(self, app):
        super().__init__(app, "Add popular games", 560)
        self.installed = app.get_installed()
        self.header("Add popular games",
                    "Tick the games you play. They all get the settings below - you can fine-tune any of them later.")
        top = ctk.CTkFrame(self.body, fg_color="transparent")
        top.pack(fill="x")
        self.search = entry(top, "Search games\u2026")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self.list.filter(self.search.get()))
        installed_available = [n for n in self.installed if not all(
            e.lower() in app.config["games"] for e in presets.GAMES[n]["exes"])]
        if installed_available:
            secondary_button(top, f"Select installed ({len(installed_available)})",
                             lambda: self.list.select(installed_available), width=170).pack(side="left",
                                                                                           padx=(8, 0))
        self.list = GameChecklist(self.body, app, self.installed, height=260, on_change=self.update_button)
        self.list.pack(fill="x", pady=(10, 0))

        template = next(iter(app.config["games"].values()), None)
        self.res = ResolutionControl(self.body, app, app.primary,
                                     template or resvib.desktop_profile(app.config, app.primary))
        self.res.pack(fill="x", pady=(18, 0))
        self.vib = vibrance_slider(self.body, template.get("vibrance", 80) if template else 80,
                                   app.color.supports_vibrance(app.primary))
        self.vib.pack(fill="x", pady=(18, 0))
        self.add_button = self.footer("Add games", self.ok)
        self.update_button()
        self.after(150, self.search.focus_set)

    def update_button(self):
        n = len(self.list.selected())
        self.add_button.configure(text=f"Add {n} game{'s' if n != 1 else ''}" if n else "Add games",
                                  state="normal" if n else "disabled")

    def ok(self):
        names = self.list.selected()
        if not names:
            return
        width, height, refresh = self.res.get()
        profile = {"width": width, "height": height, "refresh": refresh, "vibrance": self.vib.get(), "alt_tab": True}
        self.result = {}
        for name in names:
            launch = self.installed.get(name, {}).get("launch")
            for exe in presets.GAMES[name]["exes"]:
                self.result[exe.lower()] = {**profile, **({"launch": launch} if launch else {})}
        self.destroy()


class ShareDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Share profiles", 540)
        self.header("Share profiles", "Send your game setups to friends or import theirs. Codes include resolution, "
                                      "vibrance and colour settings - not launch paths or hotkeys.")
        self.section(self.body, "Your share code")
        self.code = ctk.CTkTextbox(self.body, height=74, fg_color=C["input"], text_color=C["text"],
                                   corner_radius=10, font=font(11, mono=True), wrap="char")
        self.code.pack(fill="x")
        self.code.insert("1.0", encode_share_code(app.config["games"]) if app.config["games"] else
                         "Add some games first.")
        self.code.configure(state="disabled")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        primary_button(row, "Copy code", self.copy, width=120, height=34).pack(side="left")
        secondary_button(row, "Export file\u2026", self.export, width=120, height=34).pack(side="left", padx=(8, 0))

        self.section(self.body, "Import")
        self.paste = ctk.CTkTextbox(self.body, height=74, fg_color=C["input"], text_color=C["text"],
                                    corner_radius=10, font=font(11, mono=True), wrap="char")
        self.paste.pack(fill="x")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        primary_button(row, "Import code", self.import_code, width=120, height=34).pack(side="left")
        secondary_button(row, "Import file\u2026", self.import_file, width=120, height=34).pack(side="left",
                                                                                              padx=(8, 0))
        self.status = note(self.body, "", 480)
        self.status.pack(fill="x", pady=(10, 0))
        self.footer("Done", self.close, cancel=False)

    def say(self, text, ok=True):
        self.status.configure(text=text, text_color=C["green"] if ok else C["danger"])

    def copy(self):
        if not self.app.config["games"]:
            return
        self.clipboard_clear()
        self.clipboard_append(encode_share_code(self.app.config["games"]))
        self.say("Copied to clipboard.")

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export game profiles", defaultextension=".json",
                                            initialfile="game-profiles.json", filetypes=[("Profiles", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"app": "ResVibranceSwitch", "version": 1,
                           "games": shareable(self.app.config["games"])}, f, indent=2)
            self.say(f"Exported {len(self.app.config['games'])} profile(s).")

    def _import(self, games):
        if not games:
            self.say("No game profiles found in that.", ok=False)
            return
        self.app.import_games(games)
        names = sorted({game_name(e) for e in games})
        self.say(f"Imported {', '.join(names)}.")

    def import_code(self):
        try:
            self._import(decode_share_code(self.paste.get("1.0", "end")))
        except ValueError as e:
            self.say(str(e), ok=False)

    def import_file(self):
        path = filedialog.askopenfilename(parent=self, title="Import game profiles",
                                          filetypes=[("Profiles", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._import(validate_games(data.get("games", data)))
        except (OSError, ValueError, AttributeError):
            self.say("Couldn't read that file.", ok=False)


class SettingsDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Settings", 540)
        self.header("Settings")
        cfg = app.config

        self.section(self.body, "General")
        self.vars = {}
        for key, text, value in (("auto_watch", "Start watching when the app opens", cfg["auto_watch"]),
                                 ("startup", "Start with Windows (minimised to the tray)", get_startup_enabled()),
                                 ("notifications", "Show a notification when profiles switch", cfg["notifications"]),
                                 ("check_updates", "Check for updates", cfg["check_updates"])):
            var = tk.BooleanVar(value=value)
            self.vars[key] = var
            switch(self.body, text, var, lambda k=key: app.set_option(k, self.vars[k].get())).pack(anchor="w",
                                                                                                  pady=4)
        if not UPDATE_REPO:
            note(self.body, "Update checks need a GitHub repository - set UPDATE_REPO in version.py.").pack(
                fill="x", pady=(0, 4))

        self.section(self.body, "Global hotkeys")
        grid = ctk.CTkFrame(self.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure(1, weight=1)
        for i, (action, text) in enumerate((("toggle_watch", "Pause / resume watching"),
                                            ("desktop", "Switch to desktop profile"),
                                            ("show", "Show this window"))):
            ctk.CTkLabel(grid, text=text, font=font(13), text_color=C["text"], anchor="w").grid(
                row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            HotkeyButton(grid, cfg["hotkeys"].get(action, ""),
                         on_change=lambda v, a=action: app.set_hotkey(a, v)).grid(row=i, column=1, sticky="ew",
                                                                                 pady=4)
        note(self.body, "Per-game hotkeys are under Edit > Launch & hotkey.").pack(fill="x", pady=(4, 0))

        self.section(self.body, "About")
        note(self.body, f"{APP_NAME} {__version__}\nSettings are stored in {resvib.DATA_DIR}", 480,
             C["text"]).pack(fill="x")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        secondary_button(row, "Open settings folder", lambda: os.startfile(resvib.DATA_DIR), height=34,
                         width=170).pack(side="left")
        secondary_button(row, "Run setup again", self.rerun_setup, height=34, width=150).pack(side="left",
                                                                                           padx=(8, 0))
        self.footer("Done", self.close, cancel=False)

    def rerun_setup(self):
        self.destroy()
        self.app.root.after(100, self.app.run_wizard)


class SetupWizard(Dialog):
    """First-run setup: desktop resolution, games, and preferences."""

    STEPS = ("Welcome", "Desktop", "Games", "Finish")

    def __init__(self, app):
        super().__init__(app, f"Welcome to {APP_NAME}", 620)
        self.installed = app.get_installed()
        self.step = 0

        self.dots = ctk.CTkFrame(self.body, fg_color="transparent")
        self.dots.pack(fill="x")
        self.pages = ctk.CTkFrame(self.body, fg_color="transparent", height=560, width=568)
        self.pages.pack(fill="x", pady=(14, 0))
        self.pages.pack_propagate(False)
        self.frames = [self.page_welcome(), self.page_desktop(), self.page_games(), self.page_finish()]

        nav = ctk.CTkFrame(self.body, fg_color="transparent")
        nav.pack(fill="x", pady=(18, 0))
        small_button(nav, "Skip setup", self.skip, width=90).pack(side="left")
        self.next_button = primary_button(nav, "Next", self.next, width=130)
        self.next_button.pack(side="right")
        self.back_button = secondary_button(nav, "Back", self.back, width=100)
        self.back_button.pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self.next())
        self.show_step()

    def page(self):
        return ctk.CTkFrame(self.pages, fg_color="transparent")

    def page_welcome(self):
        f = self.page()
        logo = ctk.CTkImage(light_image=make_icon_image(160), dark_image=make_icon_image(160), size=(96, 96))
        ctk.CTkLabel(f, image=logo, text="").pack(pady=(30, 16))
        ctk.CTkLabel(f, text=f"Welcome to {APP_NAME}", font=font(24, "bold", display=True),
                     text_color=C["text"]).pack()
        ctk.CTkLabel(f, text="Your games get their own resolution and colours - automatically.", font=font(14),
                     text_color=C["muted"]).pack(pady=(4, 26))
        for title, text in (("Automatic switching", "Stretched res and vibrance the moment a game launches."),
                            ("Smart alt-tab", "Normal colours on the desktop, punchy colours in-game."),
                            ("Safe", "Your desktop settings come back when the game closes - even after a crash.")):
            row = ctk.CTkFrame(f, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text="\u2713", width=30, height=30, corner_radius=15, fg_color=C["accent_soft"],
                         text_color=C["accent_text"], font=font(14, "bold")).pack(side="left", padx=14, pady=12)
            text_box = ctk.CTkFrame(row, fg_color="transparent")
            text_box.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_box, text=title, font=font(13, "bold"), text_color=C["text"], anchor="w").pack(fill="x")
            ctk.CTkLabel(text_box, text=text, font=font(12), text_color=C["muted"], anchor="w", justify="left",
                         wraplength=440).pack(fill="x", padx=(0, 14))
        return f

    def page_desktop(self):
        f = self.page()
        app = self.app
        self.page_title(f, "Your desktop", "The resolution and colours to use when you're not gaming. "
                                           "This is usually your monitor's native resolution.")
        display = next(d for d in resvib.list_displays() if d.name == app.primary)
        badge(f, f"  {display.label}  ", C["chip"], C["text"], height=28).pack(anchor="w", pady=(0, 16))
        native = dict(zip(("width", "height", "refresh"), resvib.list_modes(app.primary)[0]))
        self.desk_res = ResolutionControl(f, app, app.primary, native)
        self.desk_res.pack(fill="x")
        self.desk_vib = vibrance_slider(f, 50, app.color.supports_vibrance(app.primary))
        self.desk_vib.pack(fill="x", pady=(18, 0))
        if not app.color.supports_vibrance(app.primary):
            note(f, "Digital vibrance needs an NVIDIA or AMD graphics card driving this monitor.").pack(
                fill="x", pady=(10, 0))
        return f

    def page_games(self):
        f = self.page()
        app = self.app
        found = sum(1 for n in self.installed
                    if not all(e.lower() in app.config["games"] for e in presets.GAMES[n]["exes"]))
        self.page_title(f, "Your games", f"We found {found} installed game{'s' if found != 1 else ''} and ticked "
                                         f"{'them' if found != 1 else 'it'}. Pick the settings to use in-game."
                        if found else "Tick the games you play and pick the settings to use in-game.")
        self.games = GameChecklist(f, app, self.installed, preselect_installed=True, height=230)
        self.games.pack(fill="x")
        current = dict(zip(("width", "height", "refresh"), resvib.get_mode(app.primary)))
        self.game_res = ResolutionControl(f, app, app.primary, current)
        self.game_res.pack(fill="x", pady=(16, 0))
        self.game_vib = vibrance_slider(f, 80, app.color.supports_vibrance(app.primary))
        self.game_vib.pack(fill="x", pady=(16, 0))
        return f

    def page_finish(self):
        f = self.page()
        self.page_title(f, "Almost done", "A few preferences - you can change them any time in Settings.")
        self.opt = {key: tk.BooleanVar(value=True) for key in ("alt_tab", "startup", "auto_watch", "notifications")}
        for key, title, text in (
                ("alt_tab", "Desktop colours when alt-tabbed", "Vibrance drops back while you're in Discord or a "
                                                               "browser, and returns when you go back to the game."),
                ("startup", "Start with Windows", "Runs quietly in the system tray so switching just works."),
                ("auto_watch", "Start watching right away", "Switch automatically as soon as the app opens."),
                ("notifications", "Switch notifications", "A small notification when a profile is applied.")):
            row = ctk.CTkFrame(f, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
            row.pack(fill="x", pady=5)
            text_box = ctk.CTkFrame(row, fg_color="transparent")
            text_box.pack(side="left", fill="x", expand=True, padx=16, pady=12)
            ctk.CTkLabel(text_box, text=title, font=font(13, "bold"), text_color=C["text"], anchor="w").pack(fill="x")
            ctk.CTkLabel(text_box, text=text, font=font(12), text_color=C["muted"], anchor="w", justify="left",
                         wraplength=420).pack(fill="x")
            switch(row, "", self.opt[key]).pack(side="right", padx=(0, 6))
        return f

    def page_title(self, parent, title, text):
        ctk.CTkLabel(parent, text=title, font=font(20, "bold", display=True), text_color=C["text"],
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(parent, text=text, font=font(13), text_color=C["muted"], anchor="w", justify="left",
                     wraplength=560).pack(fill="x", pady=(2, 16))

    def show_step(self):
        for frame in self.frames:
            frame.pack_forget()
        self.frames[self.step].pack(fill="both", expand=True)
        for child in self.dots.winfo_children():
            child.destroy()
        for i, name in enumerate(self.STEPS):
            done, current = i < self.step, i == self.step
            ctk.CTkLabel(self.dots, text=f"  {i + 1}  {name}  ", height=26, corner_radius=13,
                         font=font(12, "bold" if current else "normal"),
                         fg_color=C["accent"] if current else C["accent_soft"] if done else C["chip"],
                         text_color="#ffffff" if current else C["accent_text"] if done else C["muted"]).pack(
                side="left", padx=(0, 6))
        self.back_button.configure(state="normal" if self.step else "disabled")
        self.next_button.configure(text="Finish" if self.step == len(self.STEPS) - 1 else
                                   "Get started" if self.step == 0 else "Next")

    def next(self):
        if self.step < len(self.STEPS) - 1:
            self.step += 1
            self.show_step()
        else:
            self.finish()

    def back(self):
        if self.step:
            self.step -= 1
            self.show_step()

    def skip(self):
        self.result = {"skipped": True}
        self.destroy()

    def finish(self):
        w, h, r = self.desk_res.get()
        desktop = {"width": w, "height": h, "refresh": r}
        if self.app.color.supports_vibrance(self.app.primary):
            desktop["vibrance"] = self.desk_vib.get()
        gw, gh, gr = self.game_res.get()
        game = {"width": gw, "height": gh, "refresh": gr, "alt_tab": self.opt["alt_tab"].get()}
        if self.app.color.supports_vibrance(self.app.primary):
            game["vibrance"] = self.game_vib.get()
        games = {}
        for name in self.games.selected():
            launch = self.installed.get(name, {}).get("launch")
            for exe in presets.GAMES[name]["exes"]:
                games[exe.lower()] = {**game, **({"launch": launch} if launch else {})}
        self.result = {"desktop": desktop, "games": games,
                       **{k: v.get() for k, v in self.opt.items() if k != "alt_tab"}}
        self.destroy()

    close = skip


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class App:
    def __init__(self, start_minimized):
        self.config = resvib.load_config()
        ctk.set_appearance_mode(self.config.get("theme", "dark"))
        self.events = queue.Queue()
        self.color = resvib.Color()
        self.primary = resvib.primary_display_name()
        self.mode_cache = {}
        self.installed = None
        self.update_info = None
        self.last_event = None
        self.tray_state = None
        self.tray_hint_shown = False
        self.shown_state = None
        self.watcher = resvib.Watcher(self.config, self.color, log=self.log, namer=game_name,
                                      on_switch=lambda event, exe: self.events.put(lambda: self.on_switch(event, exe)))

        self.root = ctk.CTk(fg_color=C["bg"])
        if start_minimized:
            self.root.withdraw()
        self.root.title(APP_NAME)
        self.place_window(980, 900)
        self.root.minsize(920, 680)
        self.icon_path = icon_file()
        self.root.iconbitmap(self.icon_path)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.protocol("WM_SAVE_YOURSELF", self.session_ending)  # Windows sign-out / shutdown
        self.desk_display = self.primary

        self.build_ui()
        self.render_games()
        self.setup_tray()
        self.start_background_services()

        self.root.after(100, self.process_events)
        self.update_status()
        if not self.config.get("setup_complete"):
            self.root.after(400, lambda: self.run_wizard(first_run=True))
        else:
            self.after_setup()

    def after_setup(self):
        self.recover_from_crash()
        if self.config.get("auto_watch", True):
            self.watcher.start()

    # ---- background services --------------------------------------------

    def start_background_services(self):
        self.events_thread = winevents.EventThread(
            on_foreground=self.watcher.poke,
            on_hotkey=lambda action: self.events.put(lambda: self.run_hotkey(action)),
            on_hotkey_error=lambda keys: self.log(
                f"Hotkey {', '.join(winevents.format_hotkey(k) for k in keys)} is already used by another app"))
        self.events_thread.start()
        self.register_hotkeys()

        self.show_event = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        threading.Thread(target=self._wait_for_show_requests, daemon=True, name="show-requests").start()

        def scan():
            try:
                found = detect.scan()
            except Exception:
                found = {}
            self.events.put(lambda: setattr(self, "installed", found))
        threading.Thread(target=scan, daemon=True, name="game-scan").start()

        if self.config.get("check_updates", True) and UPDATE_REPO:
            def check():
                try:
                    info = updater.check()
                except Exception:
                    info = None
                if info:
                    self.events.put(lambda: self.show_update(info))
            threading.Thread(target=check, daemon=True, name="update-check").start()

        backends = ", ".join(b.NAME for b in self.color.backends) or "none found"
        self.log(f"Vibrance support: {backends}")

    def _wait_for_show_requests(self):
        while True:
            if kernel32.WaitForSingleObject(self.show_event, 1000) == 0:
                self.events.put(self.show)

    def get_installed(self):
        if self.installed is None:
            try:
                self.installed = detect.scan()
            except Exception:
                self.installed = {}
        return self.installed

    def register_hotkeys(self):
        mapping = {action: key for action, key in self.config["hotkeys"].items() if key}
        for _name, exes in self.game_groups():
            key = self.config["games"][exes[0]].get("hotkey")
            if key:
                mapping[f"game:{exes[0]}"] = key
        self.events_thread.set_hotkeys(mapping)

    def run_hotkey(self, action):
        if action == "toggle_watch":
            self.toggle_watching()
            self.notify("Watching" if self.watcher.running else "Paused - profiles won't switch automatically")
        elif action == "desktop":
            if self.apply_desktop():
                self.notify("Desktop profile applied")
        elif action == "show":
            self.show()
        elif action.startswith("game:") and action[5:] in self.config["games"]:
            exe = action[5:]
            if self.manual_apply(exe):
                self.notify(f"{game_name(exe)} profile applied")

    def recover_from_crash(self):
        leftover = resvib.read_state()
        if not leftover:
            return
        connected = {d.name for d in resvib.list_displays()}
        self.apply_desktop([d for d in leftover if d in connected], quiet=True)
        for display in leftover:
            resvib.clear_game_state(display)
        self.log("Restored your desktop settings after an unexpected exit")

    # ---- window placement & UI construction ------------------------------

    def place_window(self, width, height):
        """Centre on the main screen, shrinking to fit short screens (e.g. 1080p at 125% scaling)."""
        root = self.root
        scale = root._get_window_scaling()  # CustomTkinter scales geometry values by this
        sw, sh = root.winfo_screenwidth() / scale, root.winfo_screenheight() / scale
        width, height = min(width, int(sw) - 20), min(height, int(sh) - 90)
        x, y = int((sw - width) / 2), max(0, int((sh - height) / 2) - 25)
        root.geometry(f"{width}x{height}+{x}+{y}")

    def build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)

        # Vibrance gradient strip along the top edge
        self.strip = tk.Canvas(root, height=4, highlightthickness=0, bd=0)
        self.strip.grid(row=0, column=0, sticky="ew")
        self.strip.bind("<Configure>", self.draw_strip)

        # Header
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=1, column=0, sticky="ew", padx=28, pady=(16, 0))
        header.grid_columnconfigure(1, weight=1)
        logo = ctk.CTkImage(light_image=make_icon_image(96), dark_image=make_icon_image(96), size=(46, 46))
        ctk.CTkLabel(header, image=logo, text="").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ctk.CTkLabel(header, text=APP_NAME, font=font(22, "bold", display=True),
                     text_color=C["text"]).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(header, text="Automatic resolution & digital vibrance for every game", font=font(13),
                     text_color=C["muted"]).grid(row=1, column=1, sticky="nw")
        theme = card(header, corner_radius=12)
        theme.grid(row=0, column=2, rowspan=2, sticky="e")
        self.theme_icon = ctk.CTkLabel(theme, text="", font=font(15), text_color=C["accent_text"], width=18)
        self.theme_icon.pack(side="left", padx=(14, 2), pady=9)
        self.theme_var = tk.BooleanVar(value=self.config.get("theme", "dark") == "dark")
        switch(theme, "Dark mode", self.theme_var, self.toggle_theme).pack(side="left", padx=(6, 14), pady=9)
        self.update_theme_icon()

        # Update banner (hidden until an update is found)
        self.banner = ctk.CTkFrame(root, fg_color=C["accent_soft"], corner_radius=12)
        self.banner_text = ctk.CTkLabel(self.banner, text="", font=font(13, "bold"), text_color=C["accent_text"])
        self.banner_text.pack(side="left", padx=16, pady=10)
        small_button(self.banner, "Dismiss", lambda: self.banner.grid_forget(), width=70).pack(side="right",
                                                                                               padx=(0, 10))
        primary_button(self.banner, "Download", self.open_update, height=30, width=100).pack(side="right",
                                                                                           padx=(0, 6))

        content = ctk.CTkFrame(root, fg_color="transparent")
        content.grid(row=3, column=0, sticky="nsew", padx=28, pady=(14, 0))
        content.grid_columnconfigure(0, weight=0, minsize=320)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(1, weight=1)

        self.build_hero(content)
        left = ctk.CTkFrame(content, fg_color="transparent", width=320)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 16), pady=(16, 0))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        self.build_desktop(left)
        self.build_activity(left)
        self.build_games(content)
        self.build_footer(root)

    def draw_strip(self, _=None):
        self.strip.delete("all")
        w = max(self.strip.winfo_width(), 1)
        for x in range(0, w, 2):
            self.strip.create_rectangle(x, 0, x + 2, 4, width=0, fill="#%02x%02x%02x" % gradient_color(x / w))

    def card_header(self, parent, title, subtitle=None, pady=(16, 10)):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=pady)
        ctk.CTkLabel(head, text=title, font=font(16, "bold", display=True), text_color=C["text"],
                     anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, font=font(12), text_color=C["muted"], anchor="w").pack(fill="x")
        return head

    def build_hero(self, parent):
        hero = card(parent)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(hero, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        top.grid_columnconfigure(0, weight=1)
        self.pill = badge(top, "", C["chip"], C["muted"], height=24)
        self.pill.grid(row=0, column=0, sticky="w")
        self.hero_title = ctk.CTkLabel(top, text="", font=font(28, "bold", display=True), text_color=C["text"])
        self.hero_title.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.hero_sub = ctk.CTkLabel(top, text="", font=font(13), text_color=C["muted"])
        self.hero_sub.grid(row=2, column=0, sticky="w")
        self.watch_button = primary_button(top, "Start watching", self.toggle_watching, width=190, height=48)
        self.watch_button.configure(font=font(14, "bold"))
        self.watch_button.grid(row=0, column=1, rowspan=3, sticky="e")

        tiles = ctk.CTkFrame(hero, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew", padx=24, pady=(14, 18))
        for i in range(3):
            tiles.grid_columnconfigure(i, weight=1, uniform="tile")
        self.tile_res, self.tile_res_caption, _ = self.stat_tile(tiles, 0, "RESOLUTION")
        self.tile_hz, _, _ = self.stat_tile(tiles, 1, "REFRESH RATE")
        self.tile_vib, _, self.vib_bar = self.stat_tile(tiles, 2, "DIGITAL VIBRANCE", bar=True)

    def stat_tile(self, parent, col, title, bar=False):
        tile = ctk.CTkFrame(parent, fg_color=C["tile"], corner_radius=12)
        tile.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 2 else 6))
        title_label = ctk.CTkLabel(tile, text=title, font=font(11, "bold"), text_color=C["muted"])
        title_label.pack(anchor="w", padx=18, pady=(8, 0))
        value = ctk.CTkLabel(tile, text="\u2013", font=font(22, "bold", display=True), text_color=C["text"])
        value.pack(anchor="w", padx=18, pady=(0, 2 if bar else 10))
        progress = None
        if bar:
            progress = ctk.CTkProgressBar(tile, height=6, corner_radius=3, progress_color=C["accent"],
                                          fg_color=C["input_btn"])
            progress.pack(fill="x", padx=18, pady=(0, 12))
        return value, title_label, progress

    def build_desktop(self, parent):
        box = card(parent)
        box.grid(row=0, column=0, sticky="ew")
        self.card_header(box, "Desktop", "Used whenever no game is running", pady=(14, 8))
        displays = resvib.list_displays()
        if len(displays) > 1:
            self.desk_display_names = {f"Display {d.number}" + (" (main)" if d.primary else ""): d.name
                                       for d in displays}
            self.desk_display_var = tk.StringVar(value=next(k for k, v in self.desk_display_names.items()
                                                            if v == self.desk_display))
            ctk.CTkSegmentedButton(box, values=list(self.desk_display_names), variable=self.desk_display_var,
                                   command=self.desktop_display_changed, font=font(12), height=30,
                                   corner_radius=8, fg_color=C["chip"], selected_color=C["accent"],
                                   selected_hover_color=C["accent_hover"], unselected_color=C["chip"],
                                   unselected_hover_color=C["chip_hover"], text_color=C["text"]).pack(
                fill="x", padx=20, pady=(0, 12))
        self.desk_body = ctk.CTkFrame(box, fg_color="transparent")
        self.desk_body.pack(fill="x", padx=20, pady=(0, 14))
        self.build_desktop_body()

    def build_desktop_body(self):
        for child in self.desk_body.winfo_children():
            child.destroy()
        display = self.desk_display
        profile = resvib.desktop_profile(self.config, display)
        self.desk_res = ResolutionControl(self.desk_body, self, display, profile,
                                          on_change=lambda: self.desktop_changed(resolution=True))
        self.desk_res.pack(fill="x")
        supported = self.color.supports_vibrance(display)
        value = profile.get("vibrance", self.color.get_vibrance(display) if supported else 50)
        self.desk_vib = vibrance_slider(self.desk_body, value, supported,
                                        on_change=lambda: self.desktop_changed(resolution=False))
        self.desk_vib.pack(fill="x", pady=(10, 0))
        secondary_button(self.desk_body, "Apply now", lambda: self.apply_desktop([display]), height=34).pack(
            fill="x", pady=(12, 0))

    def desktop_display_changed(self, label):
        self.desk_display = self.desk_display_names[label]
        self.build_desktop_body()

    def build_activity(self, parent):
        box = card(parent)
        box.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        self.card_header(box, "Activity", pady=(12, 6))
        self.log_box = ctk.CTkTextbox(box, height=40, fg_color=C["tile"], text_color=C["muted"], corner_radius=10,
                                      font=font(11, mono=True), wrap="word", border_spacing=4,
                                      scrollbar_button_color=C["chip"])
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

    def build_games(self, parent):
        box = card(parent)
        box.grid(row=1, column=1, sticky="nsew", pady=(16, 0))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 10))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        line = ctk.CTkFrame(titles, fg_color="transparent")
        line.pack(anchor="w")
        ctk.CTkLabel(line, text="Games", font=font(16, "bold", display=True), text_color=C["text"]).pack(side="left")
        self.count_badge = badge(line, "", C["accent_soft"], C["accent_text"], width=26)
        self.count_badge.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(titles, text="Switches automatically while one is running", font=font(12),
                     text_color=C["muted"]).pack(anchor="w")
        secondary_button(head, "Share", lambda: ShareDialog(self).show(), width=70, height=34).pack(side="right")
        secondary_button(head, "+  Custom", self.add_game, width=92, height=34).pack(side="right", padx=(0, 8))
        primary_button(head, "+  Popular", self.add_presets, width=104, height=34).pack(side="right", padx=(0, 8))

        self.game_list = ctk.CTkScrollableFrame(box, fg_color="transparent", scrollbar_button_color=C["chip"],
                                                scrollbar_button_hover_color=C["chip_hover"])
        self.game_list.pack(fill="both", expand=True, padx=(12, 8), pady=(0, 12))

    def build_footer(self, root):
        bar = card(root, corner_radius=12)
        bar.grid(row=4, column=0, sticky="ew", padx=28, pady=(14, 18))
        self.auto_watch = tk.BooleanVar(value=self.config.get("auto_watch", True))
        self.startup = tk.BooleanVar(value=get_startup_enabled())
        switch(bar, "Watch on launch", self.auto_watch,
               lambda: self.set_option("auto_watch", self.auto_watch.get())).pack(side="left", padx=(18, 6), pady=10)
        switch(bar, "Start with Windows", self.startup,
               lambda: self.set_option("startup", self.startup.get())).pack(side="left", padx=(12, 6), pady=10)
        ctk.CTkLabel(bar, text=f"v{__version__}", font=font(12), text_color=C["muted"]).pack(side="right",
                                                                                          padx=(6, 18))
        secondary_button(bar, "Settings", lambda: SettingsDialog(self).show(), height=32, width=90).pack(
            side="right")

    # ---- game list -------------------------------------------------------

    def game_groups(self):
        groups = {}
        for exe in self.config["games"]:
            groups.setdefault(game_name(exe), []).append(exe)
        return sorted(((n, sorted(e)) for n, e in groups.items()), key=lambda g: g[0].lower())

    def render_games(self):
        for child in self.game_list.winfo_children():
            child.destroy()
        groups = self.game_groups()
        self.count_badge.configure(text=str(len(groups)))
        live = self.watcher.active if self.watcher.running else None
        if not groups:
            empty = ctk.CTkFrame(self.game_list, fg_color="transparent")
            empty.pack(expand=True, pady=70)
            ctk.CTkLabel(empty, text="No games yet", font=font(18, "bold", display=True),
                         text_color=C["text"]).pack()
            ctk.CTkLabel(empty, text="Add Counter-Strike, Valorant, Apex, Call of Duty and more\n"
                                     "with a couple of clicks.", font=font(13), text_color=C["muted"],
                         justify="center").pack(pady=(4, 14))
            primary_button(empty, "+  Add popular games", self.add_presets, width=190).pack()
            return
        for name, exes in groups:
            self.game_row(name, exes, live in exes)

    def game_row(self, name, exes, live):
        p = self.config["games"][exes[0]]
        row = ctk.CTkFrame(self.game_list, fg_color=C["row"], corner_radius=12,
                           border_width=2 if live else 1, border_color=C["accent"] if live else C["border"])
        row.pack(fill="x", pady=4, padx=(0, 6))
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=initials(name), width=44, height=44, corner_radius=12,
                     fg_color=avatar_color(name), text_color="#ffffff",
                     font=font(15, "bold", display=True)).grid(row=0, column=0, rowspan=2, padx=14, pady=12)

        title = ctk.CTkFrame(row, fg_color="transparent")
        title.grid(row=0, column=1, sticky="sw", pady=(12, 0))
        ctk.CTkLabel(title, text=shorten(name, 26), font=font(14, "bold"), text_color=C["text"]).pack(side="left")
        if live:
            badge(title, "  \u25cf LIVE  ", C["green_soft"], C["green"]).pack(side="left", padx=(8, 0))
        meta = [f"{p['width']} \u00d7 {p['height']} \u00b7 {p.get('refresh', '-')} Hz"]
        if p.get("display"):
            meta.append(f"Display {''.join(ch for ch in p['display'] if ch.isdigit())}")
        if p.get("hotkey"):
            meta.append(winevents.format_hotkey(p["hotkey"]))
        meta.append(", ".join(exes))
        ctk.CTkLabel(row, text=shorten("   \u00b7   ".join(meta), 44), font=font(12), text_color=C["muted"],
                     anchor="w").grid(row=1, column=1, sticky="nw", pady=(0, 12))

        badge(row, f"{p['vibrance']}%" if "vibrance" in p else "\u2013", C["accent_soft"], C["accent_text"],
              width=50, height=28).grid(row=0, column=2, rowspan=2, padx=(8, 4))
        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=0, column=3, rowspan=2, padx=(0, 8))
        if p.get("launch"):
            small_button(actions, "\u25b6 Play", lambda: self.play_game(exes), accent=True, width=62).pack(side="left")
        small_button(actions, "Test", lambda: self.test_game(exes), width=46).pack(side="left")
        small_button(actions, "Edit", lambda: self.edit_game(name, exes), width=46).pack(side="left")
        small_button(actions, "Remove", lambda: self.remove_game(name, exes), danger=True, width=64).pack(side="left")

        for widget in (row, title):
            widget.bind("<Double-Button-1>", lambda e: self.edit_game(name, exes))

    # ---- modes -----------------------------------------------------------

    def modes_for(self, display):
        if display not in self.mode_cache:
            self.mode_cache[display] = {mode_label(*m): m for m in resvib.list_modes(display)}
        return self.mode_cache[display]

    def mode_string(self, profile, display):
        modes = self.modes_for(display)
        key = (profile["width"], profile["height"], profile.get("refresh"))
        for label, mode in modes.items():
            if mode == key:
                return label
        # Custom mode not reported by the driver (or from another monitor): still show and keep it.
        label = mode_label(*key)
        modes[label] = key
        return label

    # ---- tray ------------------------------------------------------------

    def setup_tray(self):
        def action(fn):
            return lambda: self.events.put(fn)

        def switch_items():
            items = [pystray.MenuItem("Desktop", action(self.apply_desktop))]
            for name, exes in self.game_groups():
                items.append(pystray.MenuItem(name, action(lambda e=exes[0]: self.manual_apply(e))))
            return items

        menu = pystray.Menu(
            pystray.MenuItem("Open", action(self.show), default=True),
            pystray.MenuItem(lambda item: "Pause watching" if self.watcher.running else "Start watching",
                             action(self.toggle_watching)),
            pystray.MenuItem("Switch to", pystray.Menu(switch_items)),
            pystray.MenuItem("Settings\u2026", action(lambda: (self.show(), SettingsDialog(self).show()))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", action(self.quit)),
        )
        self.tray = pystray.Icon("resvib", tray_image("paused"), APP_NAME, menu)
        self.tray.run_detached()

    def set_tray_state(self, state, tooltip):
        if state != self.tray_state:
            self.tray_state = state
            self.tray.icon = tray_image(state)
        self.tray.title = tooltip

    def notify(self, message):
        if self.config.get("notifications", True):
            try:
                self.tray.notify(message, APP_NAME)
            except Exception:
                pass

    def hide_to_tray(self):
        self.root.withdraw()
        if not self.tray_hint_shown:
            self.tray_hint_shown = True
            try:
                self.tray.notify("Still running in the system tray. Right-click the icon to quit.", APP_NAME)
            except Exception:
                pass

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def restore_everything(self):
        self.watcher.stop(restore_desktop=True)
        leftover = resvib.read_state()
        if leftover:  # profiles applied manually (Test / hotkey / tray)
            self.apply_desktop(list(leftover), quiet=True)
        self.color.reset_all_adjustments()

    def quit(self):
        self.restore_everything()
        self.events_thread.stop()
        self.tray.stop()
        self.root.destroy()

    def session_ending(self):
        self.restore_everything()

    # ---- events / logging / status --------------------------------------

    def log(self, msg):
        # Called from background threads too, so route through the queue.
        self.events.put(lambda: self._append_log(f"{time.strftime('%H:%M:%S')}  {msg}"))

    def _append_log(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def process_events(self):
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def on_switch(self, event, exe):
        if event == "game":
            self.notify(f"{game_name(exe)} detected - game profile applied")
        elif event == "desktop":
            self.notify("Game closed - desktop profile restored")

    def show_update(self, info):
        self.update_info = info
        self.banner_text.configure(text=f"Version {info['version']} is available - you have {__version__}")
        self.banner.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))

    def open_update(self):
        if self.update_info and self.update_info.get("url"):
            webbrowser.open(self.update_info["url"])

    def update_status(self):
        watching = self.watcher.running
        active = self.watcher.active if watching else None
        in_game = active not in (None, "desktop")
        display = self.watcher.active_display if in_game else self.desk_display
        try:
            w, h, hz = resvib.get_mode(display)
            self.tile_res.configure(text=f"{w} \u00d7 {h}")
            self.tile_hz.configure(text=f"{hz} Hz")
            vib = self.color.get_vibrance(display)
            self.tile_vib.configure(text=f"{vib}%" if vib is not None else "N/A")
            self.vib_bar.set((vib - 50) / 50 if vib is not None else 0)
            if len(self.config["desktop"]) > 1:
                self.tile_res_caption.configure(text=f"RESOLUTION \u00b7 DISPLAY "
                                                     f"{''.join(ch for ch in display if ch.isdigit())}")
        except RuntimeError:
            pass

        if in_game:
            focused = self.watcher.focused
            self.pill.configure(text="  \u25cf  IN GAME  ", fg_color=C["accent_soft"], text_color=C["accent_text"])
            self.hero_title.configure(text=game_name(active))
            self.hero_sub.configure(text=f"{active} is running - game profile applied" if focused else
                                    "Alt-tabbed - desktop colours until you return to the game")
            self.set_tray_state("game", f"{APP_NAME} - {game_name(active)}")
        elif watching:
            self.pill.configure(text="  \u25cf  WATCHING  ", fg_color=C["green_soft"], text_color=C["green"])
            self.hero_title.configure(text="Desktop profile")
            self.hero_sub.configure(text="Waiting for one of your games to launch")
            self.set_tray_state("watching", f"{APP_NAME} - watching")
        else:
            self.pill.configure(text="  \u275a\u275a  PAUSED  ", fg_color=C["amber_soft"], text_color=C["amber"])
            self.hero_title.configure(text="Switching paused")
            self.hero_sub.configure(text="Start watching to switch automatically when a game launches")
            self.set_tray_state("paused", f"{APP_NAME} - paused")
        if watching:
            self.watch_button.configure(text="Pause watching", fg_color=C["chip"], hover_color=C["chip_hover"],
                                        text_color=C["text"])
        else:
            self.watch_button.configure(text="Start watching", fg_color=C["accent"], hover_color=C["accent_hover"],
                                        text_color="#ffffff")
        state = (active, watching)
        if state != self.shown_state:
            self.shown_state = state
            self.render_games()
        self.root.after(1000, self.update_status)

    # ---- applying profiles ------------------------------------------------

    def report_error(self, title, error):
        self.log(f"{title}: {error}")
        ConfirmDialog(self, title, str(error), cancel=False).show()

    def apply_desktop(self, displays=None, quiet=False):
        connected = {d.name for d in resvib.list_displays()}
        displays = [d for d in (displays or self.config["desktop"]) if d in connected]
        try:
            for display in displays:
                label = "Desktop" if len(self.config["desktop"]) < 2 else \
                    f"Desktop (Display {''.join(ch for ch in display if ch.isdigit())})"
                resvib.apply_profile(resvib.desktop_profile(self.config, display), display, self.color, label,
                                     self.log)
                resvib.clear_game_state(display)
            return True
        except RuntimeError as e:
            if not quiet:
                self.report_error("Couldn't apply desktop profile", e)
            return False

    def manual_apply(self, exe):
        """Apply a game profile on demand (Test, Play, hotkey, tray). Returns the display, or None."""
        profile = self.config["games"][exe]
        display = resvib.resolve_display(profile.get("display"))
        desktop = resvib.desktop_profile(self.config, display)
        if "vibrance" not in desktop and self.color.supports_vibrance(display):
            desktop["vibrance"] = self.color.get_vibrance(display)
        try:
            resvib.mark_game_state(display, exe)
            resvib.apply_profile(profile, display, self.color, game_name(exe), self.log)
            return display
        except RuntimeError as e:
            self.report_error("Couldn't apply profile", e)
            return None

    def restore_expected(self, display):
        """Put a display back to what the watcher wants right now."""
        if self.watcher.running:
            self.watcher.update_config(self.config)
        else:
            self.apply_desktop([display])

    def test_game(self, exes):
        display = self.manual_apply(exes[0])
        if display is None:
            return
        p = self.config["games"][exes[0]]
        KeepChangesDialog(self, f"Testing {game_name(exes[0])}",
                          f"Your display is now {p['width']} \u00d7 {p['height']} at {p.get('refresh', '-')} Hz. "
                          f"If the screen looks wrong, wait and it will switch back.",
                          revert=lambda: self.restore_expected(display)).show()

    def play_game(self, exes):
        profile = self.config["games"][exes[0]]
        if self.manual_apply(exes[0]) is None:
            return
        try:
            detect.launch(profile["launch"])
            self.log(f"Launching {game_name(exes[0])}")
        except OSError as e:
            self.report_error("Couldn't launch the game", e)

    # ---- actions ---------------------------------------------------------

    def toggle_theme(self):
        mode = "dark" if self.theme_var.get() else "light"
        ctk.set_appearance_mode(mode)
        self.config["theme"] = mode
        resvib.save_config(self.config)
        self.update_theme_icon()

    def update_theme_icon(self):
        self.theme_icon.configure(text="\u263e" if self.theme_var.get() else "\u2600")

    def toggle_watching(self):
        if self.watcher.running:
            self.watcher.stop(restore_desktop=True)
        else:
            self.watcher.start()
        self.tray.update_menu()

    def set_option(self, key, value):
        if key == "startup":
            try:
                set_startup_enabled(value)
            except OSError as e:
                self.report_error("Couldn't change the startup setting", e)
            self.startup.set(get_startup_enabled())
            return
        self.config[key] = value
        resvib.save_config(self.config)
        if key == "auto_watch":
            self.auto_watch.set(value)

    def set_hotkey(self, action, value):
        self.config["hotkeys"][action] = value
        resvib.save_config(self.config)
        self.register_hotkeys()

    def save_games(self):
        """Persist game changes, re-register hotkeys and have the watcher re-apply with the new values."""
        resvib.save_config(self.config)
        self.watcher.update_config(self.config)
        self.register_hotkeys()
        self.render_games()
        self.tray.update_menu()

    def desktop_changed(self, resolution):
        display = self.desk_display
        old = dict(resvib.desktop_profile(self.config, display))
        new = dict(old)
        new["width"], new["height"], new["refresh"] = self.desk_res.get()
        if self.color.supports_vibrance(display):
            new["vibrance"] = self.desk_vib.get()
        self.config["desktop"][display] = new
        resvib.save_config(self.config)
        if self.watcher.in_game and self.watcher.active_display == display:
            self.log("Desktop profile saved - it applies when you leave the game")
            return
        if not self.apply_desktop([display]):
            return
        if resolution and (old["width"], old["height"], old.get("refresh")) != \
                (new["width"], new["height"], new["refresh"]):
            KeepChangesDialog(self, "Keep this resolution?",
                              f"Your desktop is now {new['width']} \u00d7 {new['height']} at {new['refresh']} Hz.",
                              revert=lambda: self.revert_desktop(display, old)).show()

    def revert_desktop(self, display, old):
        self.config["desktop"][display] = old
        resvib.save_config(self.config)
        self.apply_desktop([display])
        self.build_desktop_body()

    def add_presets(self):
        added = PresetDialog(self).show()
        if added:
            for exe, profile in added.items():
                self.config["games"].setdefault(exe, profile)
            self.save_games()
            self.log(f"Added {', '.join(sorted({game_name(e) for e in added}))}")

    def add_game(self):
        result = ProfileDialog(self, "Add custom game").show()
        if result:
            exes, profile = result
            self.config["games"][exes[0]] = profile
            self.save_games()
            self.log(f"Added {game_name(exes[0])}")

    def edit_game(self, name, exes):
        result = ProfileDialog(self, f"Edit {name}", exes, self.config["games"][exes[0]]).show()
        if result:
            new_exes, profile = result
            for exe in exes:
                del self.config["games"][exe]
            for exe in new_exes:
                self.config["games"][exe] = dict(profile)
            self.save_games()

    def remove_game(self, name, exes):
        if ConfirmDialog(self, f"Remove {name}?", "Its resolution and vibrance settings will be deleted.",
                         ok_text="Remove", danger=True).show():
            for exe in exes:
                del self.config["games"][exe]
            self.save_games()
            self.log(f"Removed {name}")

    def import_games(self, games):
        self.config["games"].update(games)
        self.save_games()
        self.log(f"Imported {len(games)} profile(s)")

    def run_wizard(self, first_run=False):
        self.show()
        result = SetupWizard(self).show()
        if result and not result.get("skipped"):
            self.config["desktop"][self.primary] = result["desktop"]
            self.config["games"].update(result["games"])
            for key in ("auto_watch", "notifications"):
                self.config[key] = result[key]
                if key == "auto_watch":
                    self.auto_watch.set(result[key])
            self.set_option("startup", result["startup"])
            self.build_desktop_body()
            self.log(f"Setup complete - {len(self.game_groups())} game(s) ready")
        self.config["setup_complete"] = True
        self.save_games()
        if first_run:
            self.after_setup()

    def run(self):
        self.root.mainloop()


def main():
    if already_running():
        if not signal_running_instance():
            ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running (check the system tray).",
                                             APP_NAME, 0x40)
        return
    App(start_minimized="--minimized" in sys.argv).run()


if __name__ == "__main__":
    main()
