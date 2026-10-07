"""Res & Vibrance Switch - GTK4 app: sidebar window, tray helper process, global hotkeys.

All colours come from PALETTES via the CSS template below.
"""

import base64
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import zlib
from collections import deque
from pathlib import Path
from string import Template

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:  # PyGObject 3.52+ moved it; older distros only have the GLib one
    from gi.repository import GLibUnix  # noqa: E402
    unix_signal_add = GLibUnix.signal_add
except ImportError:
    unix_signal_add = GLib.unix_signal_add

import detect  # noqa: E402
import hotkeys  # noqa: E402
import presets  # noqa: E402
import resvib  # noqa: E402
from version import __version__  # noqa: E402

APP_ID = "io.github.notreallyaccurate.ResVibranceSwitch"
APP_NAME = "Res & Vibrance Switch"
ICON_NAME = "res-vibrance-switch"
AUTOSTART_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / \
    f"{APP_ID}.desktop"
SHARE_PREFIX = "RVS1:"
SHAREABLE_KEYS = ("width", "height", "refresh", "vibrance", "brightness", "contrast", "gamma", "alt_tab")
GRADIENT = [(0x22, 0xD3, 0xEE), (0x7C, 0x5C, 0xFF), (0xEC, 0x48, 0x99), (0xF5, 0x9E, 0x0B)]

# --------------------------------------------------------------------------
# Theme: neutral greys, one accent (#007AFF), hairlines, soft shadows
# --------------------------------------------------------------------------

PALETTES = {
    "light": {
        "accent": "#007AFF", "accent_hover": "#0A6FE6", "focus": "rgba(0,122,255,0.42)", "red": "#FF3B30",
        "text": "#1d1d1f", "secondary": "#6e6e73", "tertiary": "#a1a1a6",
        "content": "#f2f2f4", "surface": "#ffffff", "sidebar": "#e6e6e9", "sidebar_glass": "rgba(232,232,236,0.74)",
        "hairline": "rgba(0,0,0,0.11)", "separator": "rgba(0,0,0,0.07)", "hover": "rgba(0,0,0,0.045)",
        "selected_idle": "rgba(0,0,0,0.09)", "control": "#ffffff", "control_hover": "#f6f6f7",
        "control_active": "#ebebed", "control_border": "rgba(0,0,0,0.14)", "field": "#ffffff",
        "track": "rgba(0,0,0,0.12)", "switch_off": "rgba(0,0,0,0.14)", "segment_track": "rgba(0,0,0,0.06)",
        "segment_on": "#ffffff", "popover": "#fbfbfc", "shadow": "rgba(0,0,0,0.07)",
        "shadow_strong": "rgba(0,0,0,0.20)", "toast": "rgba(40,40,42,0.92)", "toast_text": "#ffffff",
        "bezel": "#2c2c2e", "stand": "#c7c7cc",
    },
    "dark": {
        "accent": "#007AFF", "accent_hover": "#1A88FF", "focus": "rgba(0,122,255,0.55)", "red": "#FF453A",
        "text": "#f5f5f7", "secondary": "#98989d", "tertiary": "#636366",
        "content": "#1e1e1f", "surface": "#29292b", "sidebar": "#262628", "sidebar_glass": "rgba(36,36,38,0.70)",
        "hairline": "rgba(255,255,255,0.10)", "separator": "rgba(255,255,255,0.06)",
        "hover": "rgba(255,255,255,0.06)", "selected_idle": "rgba(255,255,255,0.10)", "control": "#3a3a3c",
        "control_hover": "#434345", "control_active": "#4d4d50", "control_border": "rgba(255,255,255,0.07)",
        "field": "#1c1c1e", "track": "rgba(255,255,255,0.16)", "switch_off": "rgba(255,255,255,0.17)",
        "segment_track": "rgba(255,255,255,0.07)", "segment_on": "#5a5a5e", "popover": "#2c2c2e",
        "shadow": "rgba(0,0,0,0.28)", "shadow_strong": "rgba(0,0,0,0.55)", "toast": "rgba(245,245,247,0.94)",
        "toast_text": "#1d1d1f", "bezel": "#0b0b0c", "stand": "#48484a",
    },
}

CSS = Template("""
* { font-family: -apple-system, system-ui, "Inter", sans-serif; }
window.main, window.sheet { color: $text; font-size: 13px; }
window.main { background: transparent; }
window.sheet { background: $content; }

/* Sidebar */
.sidebar { background: $sidebar_bg; border-right: 1px solid $hairline; }
.sidebar-head { min-height: 38px; padding: 0 16px; }
.app-name { font-weight: 600; font-size: 13px; }
.sidebar list { background: transparent; }
.sidebar list > row {
  margin: 1px 10px; padding: 5px 8px; border-radius: 6px; min-height: 0;
  transition: background-color 120ms ease-out, color 120ms ease-out;
}
.sidebar list > row:hover { background: $hover; }
.sidebar list > row image { color: $secondary; }
.sidebar list > row:selected { background: $accent; color: #ffffff; }
.sidebar list > row:selected image { color: #ffffff; }
window.main:backdrop .sidebar list > row:selected { background: $selected_idle; color: $text; }
window.main:backdrop .sidebar list > row:selected image { color: $secondary; }
.sidebar-status { padding: 12px 18px 14px; color: $secondary; font-size: 12px; }
.dot { min-width: 7px; min-height: 7px; border-radius: 4px; background: $tertiary; }
.dot.on { background: $accent; }

/* Toolbar + content */
.content { background: $content; }
headerbar.toolbar {
  min-height: 38px; padding: 0 8px; background: $content; color: $text;
  border-bottom: 1px solid $hairline; box-shadow: none;
}
window.main:backdrop headerbar.toolbar { background: $content; }
.page-title { font-weight: 600; font-size: 13px; }
.toolbar-label { color: $secondary; font-size: 12px; }

/* Text */
.row-title { font-size: 13px; }
.row-subtitle, .footer, .caption { font-size: 11px; color: $secondary; }
.group-title { font-weight: 600; font-size: 13px; margin: 0 4px; }
.value { font-size: 12px; color: $secondary; font-feature-settings: "tnum"; }
.hero-title { font-size: 15px; font-weight: 600; }
.empty-text { color: $secondary; }
.sheet-title { font-size: 15px; font-weight: 600; }
.mono { font-family: monospace; font-size: 11px; }
.dim { color: $tertiary; }
.accent-text { color: $accent; }

/* Grouped rows, like System Settings */
list.group {
  background: $surface; border: 1px solid $hairline; border-radius: 10px;
  box-shadow: 0 1px 2px $shadow;
}
list.group > row {
  padding: 7px 12px; min-height: 24px; background: transparent;
  border-bottom: 1px solid $separator; transition: background-color 120ms ease-out;
}
list.group > row:first-child { border-radius: 10px 10px 0 0; }
list.group > row:last-child { border-bottom: none; border-radius: 0 0 10px 10px; }
list.group > row:only-child { border-radius: 10px; }
list.group > row.activatable:hover { background: $hover; }
list.group > row:focus-visible { outline: 3px solid $focus; outline-offset: -3px; }
.tile {
  min-width: 28px; min-height: 28px; border-radius: 7px; background: $segment_track;
  color: $secondary; font-weight: 600; font-size: 11px;
}

/* Buttons */
button {
  min-height: 22px; padding: 1px 10px; border-radius: 6px; background: $control; color: $text;
  border: 1px solid $control_border; box-shadow: 0 1px 1px $shadow; font-weight: 400;
  transition: background-color 120ms ease-out;
}
button:hover { background: $control_hover; }
button:active, button:checked { background: $control_active; }
button:disabled { opacity: 0.45; }
button.default { background: $accent; color: #ffffff; border-color: transparent; }
button.default:hover { background: $accent_hover; }
button.destructive { color: $red; }
button.flat, headerbar button, button.image-button.flat {
  background: transparent; border-color: transparent; box-shadow: none; min-width: 24px; padding: 1px 6px;
}
button.flat:hover, headerbar button:hover { background: $hover; }
button:focus-visible, entry:focus-within, dropdown > button:focus-visible, switch:focus-visible,
checkbutton:focus-visible > check { outline: 3px solid $focus; outline-offset: 0; }
.capturing { background: $accent; color: #ffffff; border-color: transparent; }

/* Segmented control */
.segmented {
  background: $segment_track; border: 1px solid $hairline; border-radius: 7px; padding: 1px;
}
.segmented > button {
  background: transparent; border: none; box-shadow: none; border-radius: 6px;
  min-height: 20px; padding: 0 14px;
}
.segmented > button:checked { background: $segment_on; box-shadow: 0 1px 2px $shadow; }

/* Switch */
switch {
  min-width: 30px; min-height: 17px; border-radius: 9px; padding: 0; border: none;
  background: $switch_off; transition: background-color 150ms ease-out;
}
switch:checked { background: $accent; }
switch > image { color: transparent; }
switch > slider {
  min-width: 15px; min-height: 15px; margin: 1px; border-radius: 8px; border: none;
  background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.30);
}

/* Slider */
scale { padding: 6px 0; }
scale > trough { min-height: 4px; border-radius: 2px; background: $track; }
scale > trough > highlight { background: $accent; border-radius: 2px; }
scale > trough > slider {
  min-width: 15px; min-height: 15px; margin: -6px; border-radius: 8px; background: #ffffff;
  border: 1px solid $control_border; box-shadow: 0 1px 2px $shadow;
}
scale:disabled > trough > highlight { background: $tertiary; }

/* Popup buttons, menus, popovers */
dropdown > button { padding: 1px 6px 1px 10px; }
popover > contents {
  background: $popover; color: $text; border: 1px solid $hairline; border-radius: 10px;
  box-shadow: 0 8px 28px $shadow_strong; padding: 4px;
}
popover listview { background: transparent; }
popover listview > row, popover modelbutton { border-radius: 5px; padding: 3px 8px; min-height: 20px; }
popover listview > row:hover, popover listview > row:selected, popover modelbutton:hover {
  background: $accent; color: #ffffff;
}

/* Fields */
entry {
  min-height: 22px; padding: 0 8px; border-radius: 6px; background: $field; color: $text;
  border: 1px solid $control_border; box-shadow: inset 0 1px 1px $shadow;
}
checkbutton > check {
  min-width: 14px; min-height: 14px; border-radius: 4px; border: 1px solid $control_border;
  background: $field; box-shadow: none;
}
checkbutton > check:checked { background: $accent; border-color: $accent; color: #ffffff; }

/* Countdown, toast */
progressbar > trough { min-height: 4px; border-radius: 2px; background: $track; }
progressbar > trough > progress { min-height: 4px; border-radius: 2px; background: $accent; }
.toast {
  background: $toast; color: $toast_text; border-radius: 8px; padding: 7px 14px; margin-bottom: 18px;
  box-shadow: 0 6px 20px $shadow_strong; font-size: 12px;
}
""")


class Theme:
    """Renders the CSS for light or dark and follows the system setting when the user picks 'system'."""

    def __init__(self, app):
        self.app = app
        self.provider = Gtk.CssProvider()
        # Above USER priority: themed desktops (matugen, noctalia, gradience...) restyle every widget from
        # ~/.config/gtk-4.0/gtk.css, which would otherwise override this app's fixed design.
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.settings = Gtk.Settings.get_default()
        try:
            self.settings.connect("notify::gtk-interface-color-scheme", lambda *_: self.apply())
        except TypeError:
            pass  # GTK < 4.20: we read the portal once instead
        self.colors = PALETTES["light"]
        self.apply()

    def system_dark(self):
        try:
            return self.settings.props.gtk_interface_color_scheme == Gtk.InterfaceColorScheme.DARK
        except AttributeError:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                value = bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                      "org.freedesktop.portal.Settings", "ReadOne",
                                      GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                                      None, 0, 1000, None).unpack()[0]
                return value == 1
            except GLib.Error:
                return False

    def is_dark(self):
        choice = self.app.config.get("theme", "system")
        return self.system_dark() if choice == "system" else choice == "dark"

    def apply(self):
        name = "dark" if self.is_dark() else "light"
        self.colors = PALETTES[name]
        self.settings.props.gtk_application_prefer_dark_theme = name == "dark"
        # Backdrop blur is the compositor's job: only go translucent where it blurs (Hyprland).
        sidebar = self.colors["sidebar_glass"] if resvib.SESSION == "hyprland" else self.colors["sidebar"]
        self.provider.load_from_string(CSS.substitute(self.colors, sidebar_bg=sidebar))
        for widget in getattr(self.app, "themed", []):
            widget.queue_draw()


def rgba(text):
    color = Gdk.RGBA()
    color.parse(text)
    return color


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def ui(fn, *args):
    """Run fn on the GTK thread (callbacks arrive from watcher, IPC and tray threads)."""
    def run():
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


def launch_command(*args):
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    return shlex.join(base + list(args))


def action_command(action):
    return launch_command("--action", action)


def get_startup_enabled():
    return AUTOSTART_FILE.exists()


def set_startup_enabled(enabled):
    if enabled:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nIcon={ICON_NAME}\n"
                                  f"Exec={launch_command('--minimized')}\nX-GNOME-Autostart-enabled=true\n")
    else:
        AUTOSTART_FILE.unlink(missing_ok=True)


def open_path(path):
    subprocess.Popen(["xdg-open", str(path)], start_new_session=True)


def _owned(pid):
    try:
        return os.stat(f"/proc/{pid}").st_uid == os.getuid()
    except OSError:
        return False


def windowed_exes():
    """Exe names of open apps, plus any running Proton/Wine .exe (for desktops that hide windows from us)."""
    procs = resvib.running_processes()
    names = {procs[p] for p in resvib.window_pids() if p in procs}
    names |= {name for pid, name in procs.items() if name.endswith(".exe") and _owned(pid)}
    wine = {"services.exe", "winedevice.exe", "plugplay.exe", "svchost.exe", "rpcss.exe", "explorer.exe",
            "conhost.exe", "start.exe", "steam.exe", "wineboot.exe", "tabtip.exe", "rundll32.exe"}
    return sorted(names - wine - {"python", "python3", Path(sys.executable).name.lower()})


def mode_label(width, height, refresh):
    return f"{width} × {height}, {refresh} Hz"


def describe(p):
    text = f"{p['width']} × {p['height']} at {p.get('refresh', '?')} Hz"
    return text + (f", vibrance {p['vibrance']}%" if "vibrance" in p else "")


def game_name(exe):
    return presets.NAME_BY_EXE.get(exe.lower(), re.sub(r"\.exe$", "", Path(exe).name, flags=re.I))


def initials(name):
    words = re.findall(r"[A-Za-z0-9]+", name) or ["?"]
    return (words[0][0] + (words[1][0] if len(words) > 1 else words[0][1:2])).upper()


def shareable(games):
    return {exe: {k: p[k] for k in SHAREABLE_KEYS if k in p} for exe, p in games.items()}


def encode_share_code(games):
    payload = json.dumps(shareable(games), separators=(",", ":")).encode()
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode()


def validate_games(data):
    """Keep only well-formed game profiles from imported data."""
    games = {}
    for exe, p in (data or {}).items():
        if not (isinstance(exe, str) and exe.strip() and isinstance(p, dict)):
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
        raise ValueError("The clipboard doesn't hold a share code. Share codes start with RVS1:")
    try:
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(code[len(SHARE_PREFIX):])))
    except Exception as e:
        raise ValueError("The share code is damaged or incomplete.") from e
    return validate_games(data)


# --------------------------------------------------------------------------
# Small widgets
# --------------------------------------------------------------------------

def label(text, *classes, xalign=0.0, wrap=False, **kw):
    widget = Gtk.Label(label=text, xalign=xalign, **kw)
    for c in classes:
        widget.add_css_class(c)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


def button(text, callback, *classes, **kw):
    widget = Gtk.Button(label=text, **kw)
    widget.connect("clicked", lambda *_: callback())
    for c in classes:
        widget.add_css_class(c)
    return widget


def icon_button(icon, callback, tooltip, *classes):
    widget = Gtk.Button.new_from_icon_name(icon)
    widget.set_tooltip_text(tooltip)
    widget.connect("clicked", lambda *_: callback())
    widget.add_css_class("flat")
    for c in classes:
        widget.add_css_class(c)
    return widget


def hbox(*children, spacing=8, **kw):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, **kw)
    for child in children:
        box.append(child)
    return box


def vbox(*children, spacing=0, **kw):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)
    for child in children:
        box.append(child)
    return box


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def row(title, subtitle=None, *suffixes, prefix=None, on_activate=None):
    """A System Settings style row: optional prefix, title/subtitle, trailing controls, optional chevron."""
    r = Gtk.ListBoxRow(activatable=on_activate is not None, selectable=False)
    content = hbox(spacing=10)
    if prefix:
        prefix.set_valign(Gtk.Align.CENTER)
        content.append(prefix)
    text = vbox(spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
    r.title_label = label(title, "row-title", ellipsize=Pango.EllipsizeMode.END)
    text.append(r.title_label)
    r.subtitle_label = label(subtitle or "", "row-subtitle", wrap=True, visible=bool(subtitle))
    text.append(r.subtitle_label)
    content.append(text)
    for suffix in suffixes:
        suffix.set_valign(Gtk.Align.CENTER)
        content.append(suffix)
    if on_activate:
        r.on_activate = on_activate
        content.append(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim"]))
    r.set_child(content)
    return r


class Group(Gtk.Box):
    """A titled, rounded list of rows with an optional footer note."""

    def __init__(self, title=None, footer=None, suffix=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if title or suffix:
            head = hbox(label(title or "", "group-title", hexpand=True))
            if suffix:
                head.append(suffix)
            self.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["group"])
        self.list.connect("row-activated", lambda _lb, r: getattr(r, "on_activate", lambda: None)())
        self.append(self.list)
        self.footer = label(footer or "", "footer", wrap=True, visible=bool(footer), margin_start=4,
                            margin_end=4)
        self.append(self.footer)

    def add(self, r):
        self.list.append(r)
        return r


class Slider(Gtk.Box):
    """A thin slider with its value on the right; reports changes once the slider settles."""

    def __init__(self, value, lo, hi, step, fmt, on_change=None, digits=0, width=180):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.fmt, self.digits, self.on_change, self._job = fmt, digits, on_change, None
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        self.scale.set_draw_value(False)
        self.scale.set_round_digits(digits)
        self.scale.set_size_request(width, -1)
        self.scale.set_value(value)
        self.value_label = label(fmt(value), "value", xalign=1.0, width_chars=5)
        self.append(self.scale)
        self.append(self.value_label)
        self.scale.connect("value-changed", self._changed)

    def get(self):
        v = self.scale.get_value()
        return round(v, self.digits) if self.digits else int(round(v))

    def set(self, value):
        self.scale.set_value(value)

    def _changed(self, *_):
        self.value_label.set_text(self.fmt(self.get()))
        if self.on_change:
            if self._job:
                GLib.source_remove(self._job)
            self._job = GLib.timeout_add(300, self._fire)

    def _fire(self):
        self._job = None
        self.on_change()
        return GLib.SOURCE_REMOVE


class Choice(Gtk.DropDown):
    """A popup button over a list of labels."""

    def __init__(self, labels, selected=0, on_change=None):
        super().__init__(model=Gtk.StringList.new(labels))
        self.labels = labels
        self.set_selected(selected)
        self._handler = self.connect("notify::selected", lambda *_: on_change and on_change(self.get_selected()))

    def set_labels(self, labels, selected=0):
        with self.handler_block(self._handler):
            self.labels = labels
            self.set_model(Gtk.StringList.new(labels))
            self.set_selected(selected)

    def value(self):
        return self.labels[self.get_selected()]


class Segmented(Gtk.Box):
    def __init__(self, labels, active, on_change):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["segmented"],
                         halign=Gtk.Align.START)
        first = None
        for i, text in enumerate(labels):
            b = Gtk.ToggleButton(label=text, group=first)
            first = first or b
            b.set_active(i == active)
            b.connect("toggled", lambda btn, i=i: btn.get_active() and on_change(i))
            self.append(b)


class ShortcutButton(Gtk.Box):
    """Click, then press a key combination to set a global shortcut (Hyprland registers it)."""

    def __init__(self, value="", on_change=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.value, self.on_change = value or "", on_change
        self.button = button("", self.start, width_request=150)
        self.clear_button = icon_button("edit-clear-symbolic", self.clear, "Remove shortcut")
        self.append(self.button)
        self.append(self.clear_button)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.button.add_controller(keys)
        self.capturing = False
        self._render()

    def _render(self, text=None):
        self.button.set_label(text or (hotkeys.format_hotkey(self.value) if self.value else "Not set"))
        (self.button.add_css_class if self.capturing else self.button.remove_css_class)("capturing")
        self.clear_button.set_visible(bool(self.value) and not self.capturing)

    def start(self):
        self.capturing = True
        self._render("Type shortcut…")
        self.button.grab_focus()

    def _key(self, _ctl, keyval, _code, state):
        if not self.capturing:
            return False
        key = (Gdk.keyval_name(keyval) or "").lower()
        if key in ("escape",):
            self.capturing = False
            self._render()
            return True
        if key.startswith(("shift", "control", "alt", "super", "meta", "caps", "num_lock", "iso_")):
            return True
        mods = [name for name, mask in (("ctrl", Gdk.ModifierType.CONTROL_MASK), ("alt", Gdk.ModifierType.ALT_MASK),
                                        ("shift", Gdk.ModifierType.SHIFT_MASK),
                                        ("super", Gdk.ModifierType.SUPER_MASK)) if state & mask]
        if key not in hotkeys.KEYS:
            self._render("Key not supported")
            return True
        if not mods and not re.fullmatch(r"f\d+", key):
            self._render("Add Ctrl, Alt or Super")
            return True
        self.capturing = False
        self.value = "+".join(mods + [key])
        self._render()
        if self.on_change:
            self.on_change(self.value)
        return True

    def clear(self):
        self.value, self.capturing = "", False
        self._render()
        if self.on_change:
            self.on_change("")


# --------------------------------------------------------------------------
# Monitor arrangement: the Overview's picture of your displays
# --------------------------------------------------------------------------

class MonitorMap(Gtk.DrawingArea):
    """Your monitors side by side at their current resolution. Each screen's colour gets richer as its
    vibrance goes up; the one a game profile is applied to gets an accent outline."""

    def __init__(self, app):
        super().__init__(content_height=196, hexpand=True)
        self.app = app
        self.monitors = []  # (name, friendly, (w, h, hz), vibrance or None, active)
        self.set_draw_func(self.draw)

    @staticmethod
    def _rounded(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _text(self, cr, markup, cx, y, color):
        layout = self.create_pango_layout(None)
        layout.set_markup(markup, -1)
        layout.set_alignment(Pango.Alignment.CENTER)
        w, _h = layout.get_pixel_size()
        Gdk.cairo_set_source_rgba(cr, rgba(color))
        cr.move_to(cx - w / 2, y)
        PangoCairo.show_layout(cr, layout)
        return _h

    def draw(self, _area, cr, width, height):
        colors = self.app.theme.colors
        mons = self.monitors
        if not mons:
            return
        gap, label_h, top = 36, 44, 14
        avail_h = height - label_h - top - 14  # leave room for the stand
        total_w = sum(m[2][0] for m in mons)
        scale = min((width * 0.84 - gap * (len(mons) - 1)) / total_w, avail_h / max(m[2][1] for m in mons))
        x = (width - (total_w * scale + gap * (len(mons) - 1))) / 2
        for name, friendly, (mw, mh, hz), vibrance, active in mons:
            w, h = mw * scale, mh * scale
            y = top + (avail_h - h)  # bottom-aligned like monitors on a desk
            # stand
            Gdk.cairo_set_source_rgba(cr, rgba(colors["stand"]))
            cr.rectangle(x + w / 2 - 7, y + h, 14, 8)
            cr.fill()
            self._rounded(cr, x + w / 2 - 22, y + h + 7, 44, 4, 2)
            cr.fill()
            # bezel + screen
            Gdk.cairo_set_source_rgba(cr, rgba(colors["bezel"]))
            self._rounded(cr, x, y, w, h, 7)
            cr.fill()
            saturation = 0.42 + 0.58 * ((vibrance if vibrance is not None else 50) - 50) / 50
            grad = cairo.LinearGradient(x, y, x + w, y + h)
            for i, c in enumerate(GRADIENT):
                luma = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
                grad.add_color_stop_rgb(i / (len(GRADIENT) - 1),
                                        *[min(255, max(0, luma + (v - luma) * saturation)) / 255 for v in c])
            self._rounded(cr, x + 4, y + 4, w - 8, h - 8, 4)
            cr.set_source(grad)
            cr.fill()
            if active:
                Gdk.cairo_set_source_rgba(cr, rgba(colors["accent"]))
                cr.set_line_width(2)
                self._rounded(cr, x - 4, y - 4, w + 8, h + 8, 10)
                cr.stroke()
            cx = x + w / 2
            ty = y + h + 18
            ty += self._text(cr, f"<b>{GLib.markup_escape_text(name)}</b>", cx, ty, colors["text"])
            detail = f"{mw} × {mh}, {hz} Hz" + (f", {vibrance}%" if vibrance is not None else "")
            self._text(cr, f"<small>{detail}</small>", cx, ty + 1, colors["secondary"])
            x += w + gap


# --------------------------------------------------------------------------
# Sheets (modal windows without a title bar)
# --------------------------------------------------------------------------

class Sheet(Gtk.Window):
    def __init__(self, app, title, text=None, width=420):
        super().__init__(transient_for=app.win, modal=True, resizable=False, default_width=width,
                         title=title, css_classes=["sheet"])
        self.app = app
        self.set_titlebar(Gtk.Box(visible=False))
        self.body = vbox(spacing=14, margin_top=20, margin_bottom=18, margin_start=20, margin_end=20)
        self.body.append(label(title, "sheet-title"))
        if text:
            self.body.append(label(text, "empty-text", wrap=True, max_width_chars=52))
        self.set_child(self.body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, *_: keyval == Gdk.KEY_Escape and (self.cancel() or True))
        self.add_controller(keys)

    def cancel(self):
        self.close()

    def buttons(self, ok_text, on_ok, cancel_text="Cancel", destructive=False, cancel=True):
        bar = hbox(spacing=8, halign=Gtk.Align.END, margin_top=6)
        if cancel:
            bar.append(button(cancel_text, self.cancel, width_request=76))
        ok = button(ok_text, on_ok, "default", width_request=76)
        if destructive:
            ok.remove_css_class("default")
            ok.add_css_class("destructive")
        bar.append(ok)
        self.set_default_widget(ok)
        self.body.append(bar)
        return ok


class ConfirmSheet(Sheet):
    def __init__(self, app, title, text, ok_text, on_ok, destructive=False, cancel=True):
        super().__init__(app, title, text)
        self.on_ok = on_ok
        self.buttons(ok_text, self.ok, destructive=destructive, cancel=cancel)
        self.present()

    def ok(self):
        self.close()
        if self.on_ok:
            self.on_ok()


class KeepChangesSheet(Sheet):
    """'Keep these settings?' that reverts by itself - protects against modes the monitor can't show."""

    def __init__(self, app, title, text, revert, seconds=15):
        super().__init__(app, title, text)
        self.revert_fn, self.seconds, self.remaining, self.done = revert, seconds, seconds, False
        self.count = label("", "caption")
        self.bar = Gtk.ProgressBar(fraction=1.0)
        self.body.append(vbox(self.count, self.bar, spacing=6))
        self.buttons("Keep", self.keep, cancel_text="Revert")
        self._tick()
        GLib.timeout_add_seconds(1, self._tick)
        self.present()

    def _tick(self):
        if self.done:
            return GLib.SOURCE_REMOVE
        if self.remaining <= 0:
            self.cancel()
            return GLib.SOURCE_REMOVE
        self.count.set_text(f"Reverting in {self.remaining} s")
        self.bar.set_fraction(self.remaining / self.seconds)
        self.remaining -= 1
        return GLib.SOURCE_CONTINUE

    def keep(self):
        self.done = True
        self.close()

    def cancel(self):
        if not self.done:
            self.done = True
            self.close()
            self.revert_fn()


class PresetSheet(Sheet):
    """Pick popular games from a searchable checklist and give them all the same settings."""

    def __init__(self, app, preselect_installed=False):
        super().__init__(app, "Add Popular Games",
                         "Tick the games you play. They all start with the settings below; "
                         "you can fine-tune each one afterwards.", width=480)
        installed = app.get_installed()
        running = resvib.running_exes()
        configured = set(app.config["games"])
        games = sorted(((n, g) for n, g in presets.GAMES.items()
                        if not all(e.lower() in configured for e in g["exes"])),
                       key=lambda item: (item[0] not in installed, item[0].lower()))
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.body.append(self.search)
        group = Group()
        self.checks = []
        for name, game in games:
            check = Gtk.CheckButton(active=preselect_installed and name in installed)
            check.connect("toggled", lambda *_: self.update())
            status = "Running" if any(e.lower() in running for e in game["exes"]) else \
                "Installed" if name in installed else ""
            r = group.add(row(name, None, label(status, "caption"), prefix=check))
            r.set_activatable(True)
            r.on_activate = check.activate
            self.checks.append((name, check, r))
        if not games:
            group.add(row("Every game in the list is already added."))
        group.list.set_filter_func(lambda r: self.search.get_text().lower() in r.title_label.get_text().lower())
        self.search.connect("search-changed", lambda *_: group.list.invalidate_filter())
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=220,
                                      max_content_height=260, propagate_natural_height=True)
        scroller.set_child(group)
        self.body.append(scroller)

        game = next(iter(app.config["games"].values()), None)
        settings = Group()
        self.res = app.resolution_choice(app.primary, game or resvib.desktop_profile(app.config, app.primary))
        settings.add(row("Resolution", None, self.res))
        self.vib = None
        if app.color.supports_vibrance(app.primary):
            self.vib = Slider((game or {}).get("vibrance", 80), 50, 100, 1, lambda v: f"{v}%")
            settings.add(row("Vibrance", None, self.vib))
        self.body.append(settings)
        self.add_button = self.buttons("Add Games", self.ok)
        self.update()
        self.present()

    def update(self):
        n = sum(check.get_active() for _n, check, _r in self.checks)
        self.add_button.set_label(f"Add {n} Game{'s' if n != 1 else ''}" if n else "Add Games")
        self.add_button.set_sensitive(bool(n))

    def ok(self):
        names = [name for name, check, _r in self.checks if check.get_active()]
        if not names:
            return
        width, height, refresh = self.res.mode()
        profile = {"width": width, "height": height, "refresh": refresh, "alt_tab": True}
        if self.vib:
            profile["vibrance"] = self.vib.get()
        added = {}
        installed = self.app.get_installed()
        for name in names:
            launch = installed.get(name, {}).get("launch")
            for exe in presets.GAMES[name]["exes"]:
                added[exe.lower()] = {**profile, **({"launch": launch} if launch else {})}
        self.close()
        self.app.add_games(added)


class CustomSheet(Sheet):
    def __init__(self, app):
        super().__init__(app, "Add Custom Game",
                         "Enter the game's process name. Proton games use their Windows .exe name.")
        self.launch = None
        self.entry = Gtk.Entry(placeholder_text="cs2 or Overwatch.exe", hexpand=True)
        self.entry.connect("activate", lambda *_: self.ok())
        self.body.append(hbox(self.entry, button("Choose…", self.browse)))
        open_apps = windowed_exes()
        if open_apps:
            pick = Choice(["Pick an open app…"] + open_apps, 0,
                          lambda i: i and self.entry.set_text(open_apps[i - 1]))
            pick.set_halign(Gtk.Align.START)
            self.body.append(pick)
        self.buttons("Add Game", self.ok)
        self.present()

    def browse(self):
        dialog = Gtk.FileDialog(title="Choose the game's executable")

        def done(d, result):
            try:
                path = Path(d.open_finish(result).get_path())
            except GLib.Error:
                return
            self.entry.set_text(path.name)
            self.launch = str(path)
        dialog.open(self, None, done)

    def ok(self):
        exe = self.entry.get_text().strip().lower()
        if not exe:
            self.entry.grab_focus()
            return
        self.close()
        self.app.add_custom(exe, self.launch)


# --------------------------------------------------------------------------
# Tray helper process
# --------------------------------------------------------------------------

class Tray:
    def __init__(self, app):
        self.app = app
        self.proc = None
        self.last = None
        try:
            self.proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("tray.py"))],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        except OSError:
            return
        threading.Thread(target=self._read, daemon=True, name="tray").start()

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _read(self):
        for line in self.proc.stdout:
            ui(self.app.tray_command, line.strip())
        ui(self.app.log, "Tray icon unavailable - closing the window will quit the app")

    def push(self, state):
        if state != self.last and self.alive:
            self.last = state
            try:
                self.proc.stdin.write(json.dumps(state) + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def stop(self):
        if self.alive:
            self.proc.stdin.close()
            self.proc.terminate()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

PAGES = (("overview", "Overview", "video-display-symbolic"),
         ("games", "Games", "input-gaming-symbolic"),
         ("displays", "Displays", "preferences-desktop-display-symbolic"),
         ("shortcuts", "Shortcuts", "input-keyboard-symbolic"),
         ("general", "General", "emblem-system-symbolic"))


class App(Gtk.Application):
    def __init__(self, start_minimized):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.start_minimized = start_minimized
        self.config = resvib.load_config()
        self.config.setdefault("theme", "system")
        self.color = resvib.Color()
        self.primary = resvib.primary_display_name()
        self.mode_cache = {}
        self.installed = None
        self.activity = deque(maxlen=100)
        self.themed = []
        self.shown_state = None
        self.detail = None  # exes of the game open on the detail page
        self.desk_display = self.primary
        self.watcher = resvib.Watcher(self.config, self.color, log=lambda m: ui(self.log, m), namer=game_name,
                                      on_switch=lambda event, exe: ui(self.on_switch, event, exe))
        self.connect("activate", self.on_activate)

    # ---- startup -----------------------------------------------------------

    def on_activate(self, _app):
        self.hold()  # keep running in the tray when the window closes
        self.theme = Theme(self)
        Gtk.Window.set_default_icon_name(ICON_NAME)
        self.build_window()
        self.tray = Tray(self)
        self.events_thread = hotkeys.EventThread(
            on_foreground=self.watcher.poke,
            on_hotkey=lambda action: ui(self.run_hotkey, action),
            on_hotkey_error=lambda keys: ui(self.log, f"Shortcut {', '.join(map(hotkeys.format_hotkey, keys))} "
                                                      "couldn't be registered"),
            on_show=lambda: ui(self.show),
            command=action_command)
        self.events_thread.start()
        self.register_hotkeys()
        unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: self.quit_app() or GLib.SOURCE_REMOVE)

        def scan():
            try:
                found = detect.scan()
            except Exception:
                found = {}
            ui(self.scanned, found)
        threading.Thread(target=scan, daemon=True, name="game-scan").start()

        backends = ", ".join(b.NAME for b in self.color.backends) or "none"
        self.log(f"Display backend {resvib.SESSION}, vibrance via {backends}")
        first_run = not self.config.get("setup_complete")
        self.config["setup_complete"] = True
        resvib.save_config(self.config)
        self.recover_from_crash()
        if self.config.get("auto_watch", True):
            self.watcher.start()
        if first_run:
            self.navigate("games")
        if not self.start_minimized or first_run:
            self.win.present()
        self.update_status()
        GLib.timeout_add_seconds(1, self.update_status)

    def scanned(self, found):
        self.installed = found
        if not self.config["games"]:
            self.render_games()

    def get_installed(self):
        if self.installed is None:
            try:
                self.installed = detect.scan()
            except Exception:
                self.installed = {}
        return self.installed

    def recover_from_crash(self):
        leftover = resvib.read_state()
        if not leftover:
            return
        connected = {d.name for d in resvib.list_displays()}
        self.apply_desktop([d for d in leftover if d in connected], quiet=True)
        for display in leftover:
            resvib.clear_game_state(display)
        self.log("Restored your desktop settings after an unexpected exit")

    # ---- window ------------------------------------------------------------

    def build_window(self):
        self.win = Gtk.ApplicationWindow(application=self, title=APP_NAME, default_width=920, default_height=660,
                                         css_classes=["main"])
        self.win.set_size_request(760, 520)
        self.win.set_titlebar(Gtk.Box(visible=False))  # our toolbar is the title bar; no drawn chrome
        self.win.connect("close-request", self.on_close)
        self.add_actions()

        # Sidebar
        sidebar = vbox(width_request=212, hexpand=False, css_classes=["sidebar"])  # don't inherit child hexpand
        icon = ICON_NAME if Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(ICON_NAME) \
            else "video-display-symbolic"  # running from source without install.sh
        head = Gtk.WindowHandle(child=hbox(Gtk.Image(icon_name=icon, pixel_size=18),
                                           label("Res & Vibrance", "app-name"), spacing=8,
                                           css_classes=["sidebar-head"]))
        sidebar.append(head)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, vexpand=True, margin_top=4)
        for name, title, icon in PAGES:
            r = Gtk.ListBoxRow(child=hbox(Gtk.Image(icon_name=icon), label(title)))
            r.page = name
            self.nav.append(r)
        self.syncing_nav = False
        self.nav.connect("row-selected", lambda _lb, r: r and not self.syncing_nav and
                         self.navigate(r.page, from_sidebar=True))
        sidebar.append(self.nav)
        self.status_dot = Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER)
        self.status_text = label("", ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        sidebar.append(hbox(self.status_dot, self.status_text, css_classes=["sidebar-status"]))

        # Toolbar
        self.toolbar = Gtk.HeaderBar(css_classes=["toolbar"])
        self.toolbar.set_title_widget(Gtk.Box())
        self.back = icon_button("go-previous-symbolic", lambda: self.navigate("games"), "Back to Games")
        self.page_title = label("", "page-title", margin_start=6)
        self.toolbar.pack_start(self.back)
        self.toolbar.pack_start(self.page_title)
        self.watch_switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Switch profiles automatically "
                                       "when a game starts")
        self.watch_handler = self.watch_switch.connect("notify::active", lambda *_: self.toggle_watching(
            self.watch_switch.get_active()))
        self.toolbar.pack_end(hbox(label("Auto-Switch", "toolbar-label"), self.watch_switch, spacing=8,
                                   margin_end=6))
        self.games_actions = hbox(
            Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Add a game", menu_model=self.menu(
                ("Add Popular Games…", "win.add-popular"), ("Add Custom Game…", "win.add-custom"))),
            Gtk.MenuButton(icon_name="document-send-symbolic", tooltip_text="Share profiles", menu_model=self.menu(
                ("Copy Share Code", "win.copy-code"), ("Import Share Code from Clipboard", "win.import-code"),
                ("Export to File…", "win.export-file"), ("Import from File…", "win.import-file"))),
            spacing=2)
        self.toolbar.pack_end(self.games_actions)
        self.play_button = button("Test", self.test_detail)
        self.toolbar.pack_end(self.play_button)

        # Pages
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150,
                               vexpand=True)
        self.pages = {}
        for name in ("overview", "games", "game", "displays", "shortcuts", "general"):
            box = vbox(spacing=22, margin_top=20, margin_bottom=28, margin_start=28, margin_end=28)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
            self.stack.add_named(scroller, name)
            self.pages[name] = box
        self.toast = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=150,
                                  halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self.toast_label = label("", "toast", wrap=True, max_width_chars=60)
        self.toast.set_child(self.toast_label)
        overlay = Gtk.Overlay(child=self.stack)
        overlay.add_overlay(self.toast)
        content = vbox(self.toolbar, overlay, hexpand=True, css_classes=["content"])

        self.win.set_child(hbox(sidebar, content, spacing=0))
        self.build_overview()
        self.render_games()
        self.build_displays()
        self.build_shortcuts()
        self.build_general()
        self.nav.select_row(self.nav.get_row_at_index(0))

    @staticmethod
    def menu(*items):
        model = Gio.Menu()
        for text, action in items:
            model.append(text, action)
        return model

    def add_actions(self):
        for name, fn in (("add-popular", lambda: PresetSheet(self)), ("add-custom", lambda: CustomSheet(self)),
                         ("copy-code", self.copy_code), ("import-code", self.import_code),
                         ("export-file", self.export_file), ("import-file", self.import_file)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, fn=fn: fn())
            self.win.add_action(action)

    def navigate(self, page, from_sidebar=False):
        self.stack.set_visible_child_name(page)
        nav_page = "games" if page == "game" else page
        if not from_sidebar:
            self.syncing_nav = True
            self.nav.select_row(self.nav.get_row_at_index([n for n, _t, _i in PAGES].index(nav_page)))
            self.syncing_nav = False
        if page == "games":
            self.render_games()
        title = game_name(self.detail[0]) if page == "game" and self.detail else dict(
            (n, t) for n, t, _i in PAGES)[nav_page]
        self.page_title.set_text(title)
        self.back.set_visible(page == "game")
        self.games_actions.set_visible(page == "games")
        self.play_button.set_visible(page == "game")
        if page != "game":
            self.detail = None

    def on_close(self, _win):
        if self.tray.alive:
            self.win.set_visible(False)
            return True  # keep running in the tray
        self.quit_app()
        return True

    def show(self):
        self.win.present()

    def toast_message(self, text):
        self.toast_label.set_text(text)
        self.toast.set_reveal_child(True)
        GLib.timeout_add(2400, lambda: self.toast.set_reveal_child(False) or GLib.SOURCE_REMOVE)

    # ---- overview ----------------------------------------------------------

    def build_overview(self):
        page = self.pages["overview"]
        self.monitor_map = MonitorMap(self)
        self.themed.append(self.monitor_map)
        self.hero_title = label("", "hero-title")
        self.hero_text = label("", "empty-text", wrap=True)
        page.append(vbox(self.monitor_map, vbox(self.hero_title, self.hero_text, spacing=2), spacing=10))
        self.activity_group = Group("Recent Activity")
        page.append(self.activity_group)
        self.render_activity()

    def render_activity(self):
        clear(self.activity_group.list)
        for stamp, message in list(self.activity)[-6:][::-1]:
            self.activity_group.add(row(message, None, label(stamp, "caption")))
        if not self.activity:
            self.activity_group.add(row("Nothing yet", "Profile switches show up here."))

    # ---- games -------------------------------------------------------------

    def game_groups(self):
        groups = {}
        for exe in self.config["games"]:
            groups.setdefault(game_name(exe), []).append(exe)
        return sorted(((n, sorted(e)) for n, e in groups.items()), key=lambda g: g[0].lower())

    def render_games(self):
        page = self.pages["games"]
        clear(page)
        groups = self.game_groups()
        if not groups:
            installed = len(self.installed or {})
            text = (f"Found {installed} installed game{'s' if installed != 1 else ''} in Steam. "
                    if installed else "") + \
                "Add the games you play, and their resolution and vibrance switch the moment they start."
            page.append(vbox(
                Gtk.Image(icon_name="input-gaming-symbolic", pixel_size=40, css_classes=["dim"]),
                label("No games yet", "hero-title", xalign=0.5),
                label(text, "empty-text", xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER,
                      max_width_chars=48),
                hbox(button("Add Custom Game…", lambda: CustomSheet(self)),
                     button("Add Popular Games…", lambda: PresetSheet(self, preselect_installed=True),
                            "default"), halign=Gtk.Align.CENTER, margin_top=8),
                spacing=8, valign=Gtk.Align.CENTER, vexpand=True, margin_top=90))
            return
        live = self.watcher.active if self.watcher.running else None
        group = Group(footer="Profiles switch automatically while a game is running.")
        for name, exes in groups:
            p = self.config["games"][exes[0]]
            suffixes = []
            if live in exes:
                suffixes.append(label("Running", "caption", "accent-text"))
            if p.get("launch"):
                suffixes.append(icon_button("media-playback-start-symbolic", lambda e=exes: self.play_game(e),
                                            f"Apply profile and launch {name}"))
            group.add(row(name, describe(p), *suffixes,
                          prefix=label(initials(name), "tile", xalign=0.5),
                          on_activate=lambda e=exes: self.open_game(e)))
        page.append(group)

    def open_game(self, exes):
        self.detail = exes
        self.build_game_page()
        self.navigate("game")

    def build_game_page(self):
        page = self.pages["game"]
        clear(page)
        exes = self.detail
        p = self.config["games"][exes[0]]
        display = resvib.resolve_display(p.get("display"))

        main = Group()
        if len(exes) > 1:
            main.add(row("Process", ", ".join(exes)))
        else:
            entry = Gtk.Entry(text=exes[0], width_chars=22)
            entry.connect("activate", lambda e: self.rename_game(e.get_text()))
            focus = Gtk.EventControllerFocus()
            focus.connect("leave", lambda *_: self.rename_game(entry.get_text()))
            entry.add_controller(focus)
            main.add(row("Process", "Native binary or Proton .exe name", entry))
        displays = resvib.list_displays()
        if len(displays) > 1:
            names = [d.name for d in displays]
            main.add(row("Monitor", None, Choice([d.label for d in displays], names.index(display),
                                                 lambda i: self.set_game(display=None if names[i] == self.primary
                                                                         else names[i], rebuild=True))))
        res = self.resolution_choice(display, p, lambda mode: self.set_game(
            width=mode[0], height=mode[1], refresh=mode[2]))
        main.add(row("Resolution", None, res))
        if self.color.supports_vibrance(display):
            vib = Slider(p.get("vibrance", 80), 50, 100, 1, lambda v: f"{v}%")
            vib.on_change = lambda: self.set_game(vibrance=vib.get())
            main.add(row("Vibrance", "50% is your normal colours, 100% the maximum", vib))
        else:
            main.footer.set_text(self.color.vibrance_hint())
            main.footer.set_visible(True)
        if resvib.FOCUS_SUPPORTED:
            switch = Gtk.Switch(active=p.get("alt_tab", True))
            switch.connect("notify::active", lambda s, _: self.set_game(alt_tab=s.get_active()))
            main.add(row("Desktop colours when alt-tabbed", "Vibrance drops back while another app is focused",
                         switch))
        page.append(main)

        if self.color.supports_adjustment(display):
            sliders = {}
            reset = button("Reset", lambda: [sliders[k].set(v) for k, v in resvib.NEUTRAL_COLOR.items()],
                           "flat")
            colour = Group("Colour", "Applied on top of vibrance while the game is focused.", suffix=reset)
            for key, title, lo, hi, step, fmt, digits in (
                    ("brightness", "Brightness", 0, 100, 1, str, 0), ("contrast", "Contrast", 0, 100, 1, str, 0),
                    ("gamma", "Gamma", 0.5, 2.5, 0.05, lambda v: f"{v:.2f}", 2)):
                s = Slider(p.get(key, resvib.NEUTRAL_COLOR[key]), lo, hi, step, fmt, digits=digits)
                s.on_change = lambda k=key, s=s: self.set_game(**{k: s.get()})
                sliders[key] = s
                colour.add(row(title, None, s))
            page.append(colour)

        launch = Group("Launch", "The play button applies this profile first, then starts the game.")
        launch_entry = Gtk.Entry(text=p.get("launch", ""), placeholder_text="steam://rungameid/730",
                                 width_chars=26)
        launch_entry.connect("changed", lambda e: self.set_game(launch=e.get_text().strip(), quiet=True))
        launch.add(row("Launch command", "A steam:// link, a program path, or a command", launch_entry))
        command = action_command(f"game:{exes[0]}")
        if hotkeys.CAN_REGISTER:
            launch.add(row("Shortcut", "Switches to this profile from anywhere",
                           ShortcutButton(p.get("hotkey", ""), lambda v: self.set_game(hotkey=v))))
        else:
            launch.add(row("Shortcut", f"Bind this command in your desktop's keyboard settings:\n{command}",
                           icon_button("edit-copy-symbolic", lambda: self.copy_text(command, "Command copied"),
                                       "Copy command")))
        page.append(launch)

        actions = hbox(button("Remove Game…", lambda: ConfirmSheet(
            self, f"Remove {game_name(exes[0])}?", "Its resolution and vibrance settings will be deleted.",
            "Remove", self.remove_detail, destructive=True), "destructive"))
        if p.get("launch"):
            actions.append(button("Play", lambda: self.play_game(exes)))
        page.append(actions)

    def set_game(self, rebuild=False, quiet=False, **changes):
        if not self.detail:
            return
        for exe in self.detail:
            p = self.config["games"][exe]
            for key, value in changes.items():
                if value in (None, "") or (key in resvib.NEUTRAL_COLOR and value == resvib.NEUTRAL_COLOR[key]):
                    p.pop(key, None)
                else:
                    p[key] = value
        self.save_games(render=not quiet)
        if rebuild:
            self.build_game_page()

    def rename_game(self, text):
        new = text.strip().lower()
        old = self.detail[0] if self.detail and len(self.detail) == 1 else None
        if not old or not new or new == old:
            return
        if new in self.config["games"]:
            self.toast_message(f"{new} already has a profile")
            return
        self.config["games"][new] = self.config["games"].pop(old)
        self.detail = [new]
        self.page_title.set_text(game_name(new))
        self.save_games()

    def remove_detail(self):
        name = game_name(self.detail[0])
        for exe in self.detail:
            del self.config["games"][exe]
        self.navigate("games")
        self.save_games()
        self.log(f"Removed {name}")

    def add_games(self, added):
        for exe, profile in added.items():
            self.config["games"].setdefault(exe, profile)
        self.save_games()
        self.log(f"Added {', '.join(sorted({game_name(e) for e in added}))}")
        self.navigate("games")

    def add_custom(self, exe, launch):
        if exe not in self.config["games"]:
            template = next(iter(self.config["games"].values()), None)
            profile = {k: template[k] for k in ("width", "height", "refresh", "vibrance") if k in template} \
                if template else {**dict(zip(("width", "height", "refresh"), resvib.get_mode(self.primary))),
                                  "vibrance": 80}
            if launch:
                profile["launch"] = launch
            self.config["games"][exe] = profile
            self.save_games()
            self.log(f"Added {game_name(exe)}")
        self.open_game([exe])

    def save_games(self, render=True):
        resvib.save_config(self.config)
        self.watcher.update_config(self.config)
        self.register_hotkeys()
        if render:
            self.render_games()
        self.push_tray()

    # ---- sharing -----------------------------------------------------------

    def copy_text(self, text, message):
        self.win.get_clipboard().set(text)
        self.toast_message(message)

    def copy_code(self):
        if not self.config["games"]:
            self.toast_message("Add some games first")
            return
        self.copy_text(encode_share_code(self.config["games"]), "Share code copied")

    def import_games(self, games):
        if not games:
            self.toast_message("No game profiles found in that")
            return
        self.config["games"].update(games)
        self.save_games()
        names = sorted({game_name(e) for e in games})
        self.log(f"Imported {', '.join(names)}")
        self.toast_message(f"Imported {', '.join(names)}")

    def import_code(self):
        def done(clipboard, result):
            try:
                self.import_games(decode_share_code(clipboard.read_text_finish(result) or ""))
            except (ValueError, GLib.Error) as e:
                self.toast_message(str(e) if isinstance(e, ValueError) else "The clipboard is empty")
        self.win.get_clipboard().read_text_async(None, done)

    def export_file(self):
        dialog = Gtk.FileDialog(title="Export Game Profiles", initial_name="game-profiles.json")

        def done(d, result):
            try:
                path = d.save_finish(result).get_path()
            except GLib.Error:
                return
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"app": "ResVibranceSwitch", "version": 1, "games": shareable(self.config["games"])},
                          f, indent=2)
            self.toast_message(f"Exported {len(self.config['games'])} profiles")
        dialog.save(self.win, None, done)

    def import_file(self):
        def done(d, result):
            try:
                with open(d.open_finish(result).get_path(), encoding="utf-8") as f:
                    data = json.load(f)
                self.import_games(validate_games(data.get("games", data)))
            except GLib.Error:
                return
            except (OSError, ValueError, AttributeError):
                self.toast_message("Couldn't read that file")
        Gtk.FileDialog(title="Import Game Profiles").open(self.win, None, done)

    # ---- displays ----------------------------------------------------------

    def modes_for(self, display):
        if display not in self.mode_cache:
            self.mode_cache[display] = {mode_label(*m): m for m in resvib.list_modes(display)}
        return self.mode_cache[display]

    def resolution_choice(self, display, profile, on_change=None):
        modes = self.modes_for(display)
        key = (profile["width"], profile["height"], profile.get("refresh"))
        if key not in modes.values() and key[2]:
            modes[mode_label(*key)] = key  # a mode from another monitor: still show and keep it
        labels = list(modes)
        current = next((i for i, text in enumerate(labels) if modes[text] == key), 0)
        choice = Choice(labels, current, on_change and (lambda i: on_change(modes[labels[i]])))
        choice.mode = lambda: modes[choice.value()]
        return choice

    def build_displays(self):
        page = self.pages["displays"]
        clear(page)
        displays = resvib.list_displays()
        if self.desk_display not in [d.name for d in displays]:
            self.desk_display = self.primary
        if len(displays) > 1:
            names = [d.name for d in displays]
            page.append(Segmented([d.name + (" (main)" if d.primary else "") for d in displays],
                                  names.index(self.desk_display), lambda i: self.desktop_display(names[i])))
        display = self.desk_display
        profile = resvib.desktop_profile(self.config, display)
        friendly = next((d.friendly for d in displays if d.name == display), "")
        group = Group(friendly or display, "Used whenever no game is running.")
        self.desk_res = self.resolution_choice(display, profile, lambda _m: self.desktop_changed(True))
        group.add(row("Resolution", None, self.desk_res))
        self.desk_vib = None
        if self.color.supports_vibrance(display):
            value = profile.get("vibrance", self.color.get_vibrance(display))
            self.desk_vib = Slider(value, 50, 100, 1, lambda v: f"{v}%", lambda: self.desktop_changed(False))
            group.add(row("Vibrance", "50% is the driver default", self.desk_vib))
        else:
            group.footer.set_text("Used whenever no game is running. " + self.color.vibrance_hint())
        page.append(group)

    def desktop_display(self, display):
        if display != self.desk_display:
            self.desk_display = display
            GLib.idle_add(lambda: self.build_displays() or GLib.SOURCE_REMOVE)

    def desktop_changed(self, resolution):
        display = self.desk_display
        old = dict(resvib.desktop_profile(self.config, display))
        new = dict(old)
        new["width"], new["height"], new["refresh"] = self.desk_res.mode()
        if self.desk_vib:
            new["vibrance"] = self.desk_vib.get()
        self.config["desktop"][display] = new
        resvib.save_config(self.config)
        if self.watcher.in_game and self.watcher.active_display == display:
            self.toast_message("Saved. It applies when you leave the game.")
            return
        if not self.apply_desktop([display]):
            return
        if resolution and (old["width"], old["height"], old.get("refresh")) != \
                (new["width"], new["height"], new["refresh"]):
            KeepChangesSheet(self, "Keep this resolution?",
                             f"{display} is now {new['width']} × {new['height']} at {new['refresh']} Hz.",
                             revert=lambda: self.revert_desktop(display, old))

    def revert_desktop(self, display, old):
        self.config["desktop"][display] = old
        resvib.save_config(self.config)
        self.apply_desktop([display])
        self.build_displays()

    # ---- shortcuts ---------------------------------------------------------

    def build_shortcuts(self):
        page = self.pages["shortcuts"]
        actions = (("toggle_watch", "Pause or resume Auto-Switch"), ("desktop", "Switch to desktop profile"),
                   ("show", "Show this window"))
        if hotkeys.CAN_REGISTER:
            group = Group(footer="Shortcuts for individual games are on each game's page.")
            for action, title in actions:
                group.add(row(title, None, ShortcutButton(self.config["hotkeys"].get(action, ""),
                                                          lambda v, a=action: self.set_hotkey(a, v))))
        else:
            group = Group(footer="Your desktop doesn't let apps register shortcuts. Add these commands in your "
                                 "desktop's keyboard settings instead. Each game's page has its own command.")
            for action, title in actions:
                command = action_command(action)
                group.add(row(title, command, icon_button("edit-copy-symbolic", lambda c=command: self.copy_text(
                    c, "Command copied"), "Copy command")))
        page.append(group)

    def set_hotkey(self, action, value):
        self.config["hotkeys"][action] = value
        resvib.save_config(self.config)
        self.register_hotkeys()

    def register_hotkeys(self):
        mapping = {action: key for action, key in self.config["hotkeys"].items() if key}
        for _name, exes in self.game_groups():
            key = self.config["games"][exes[0]].get("hotkey")
            if key:
                mapping[f"game:{exes[0]}"] = key
        self.events_thread.set_hotkeys(mapping)

    def run_hotkey(self, action):
        if action == "toggle_watch":
            self.toggle_watching(not self.watcher.running)
            self.notify("Auto-Switch on" if self.watcher.running else "Auto-Switch paused")
        elif action == "desktop":
            if self.apply_desktop():
                self.notify("Desktop profile applied")
        elif action == "show":
            self.show()
        elif action.startswith("game:") and action[5:] in self.config["games"]:
            exe = action[5:]
            if self.manual_apply(exe):
                self.notify(f"{game_name(exe)} profile applied")

    # ---- general -----------------------------------------------------------

    def build_general(self):
        page = self.pages["general"]
        appearance = Group("Appearance")
        themes = ["system", "light", "dark"]
        appearance.add(row("Theme", None, Segmented(["System", "Light", "Dark"],
                                                    themes.index(self.config.get("theme", "system")),
                                                    lambda i: self.set_option("theme", themes[i]))))
        page.append(appearance)

        behaviour = Group("Behaviour")
        for key, title, subtitle, value in (
                ("startup", "Start on login", "Runs quietly in the tray", get_startup_enabled()),
                ("auto_watch", "Turn on Auto-Switch at launch", None, self.config.get("auto_watch", True)),
                ("notifications", "Notify when a profile switches", None, self.config.get("notifications", True))):
            switch = Gtk.Switch(active=value)
            switch.connect("notify::active", lambda s, _, k=key: self.set_option(k, s.get_active()))
            behaviour.add(row(title, subtitle, switch))
        page.append(behaviour)

        vibrance = ", ".join(b.NAME for b in self.color.backends) or "Not available"
        system = Group("This Computer", f"{APP_NAME} {__version__}")
        system.add(row("Desktop", {"hyprland": "Hyprland", "kde": "KDE Plasma", "gnome": "GNOME",
                                   "wlroots": "wlroots compositor", "x11": "X11"}[resvib.SESSION]))
        system.add(row("Vibrance", vibrance if self.color.backends else self.color.vibrance_hint()))
        system.add(row("Settings folder", str(resvib.DATA_DIR),
                       button("Open", lambda: open_path(resvib.DATA_DIR))))
        page.append(system)

    def set_option(self, key, value):
        if key == "startup":
            try:
                set_startup_enabled(value)
            except OSError as e:
                self.report_error("Couldn't change the startup setting", e)
            return
        self.config[key] = value
        resvib.save_config(self.config)
        if key == "theme":
            self.theme.apply()

    # ---- status, logging, tray ---------------------------------------------

    def log(self, msg):
        self.activity.append((time.strftime("%H:%M"), msg))
        if hasattr(self, "activity_group"):
            self.render_activity()

    def notify(self, message):
        if self.config.get("notifications", True):
            try:
                subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", ICON_NAME, APP_NAME, message],
                                 start_new_session=True)
            except OSError:
                pass  # no libnotify

    def on_switch(self, event, exe):
        if event == "game":
            self.notify(f"{game_name(exe)} detected, game profile applied")
        elif event == "desktop":
            self.notify("Game closed, desktop profile restored")

    def tray_command(self, command):
        if command == "show":
            self.show()
        elif command == "toggle":
            self.toggle_watching(not self.watcher.running)
        elif command == "desktop":
            self.apply_desktop()
        elif command.startswith("game:") and command[5:] in self.config["games"]:
            self.manual_apply(command[5:])
        elif command == "quit":
            self.quit_app()

    def push_tray(self):
        watching = self.watcher.running
        in_game = watching and self.watcher.in_game
        self.tray.push({"state": "game" if in_game else "watching" if watching else "paused",
                        "title": f"{APP_NAME}: {game_name(self.watcher.active)}" if in_game else
                        f"{APP_NAME}: {'watching' if watching else 'paused'}",
                        "watching": watching,
                        "games": [(name, exes[0]) for name, exes in self.game_groups()]})

    def update_status(self):
        watching = self.watcher.running
        active = self.watcher.active if watching else None
        in_game = active not in (None, "desktop")
        try:
            modes = resvib.current_modes()
        except RuntimeError:
            modes = {}
        displays = resvib.list_displays()
        self.monitor_map.monitors = [
            (d.name, d.friendly, modes[d.name], self.color.get_vibrance(d.name),
             in_game and self.watcher.active_display == d.name) for d in displays if d.name in modes]
        self.monitor_map.queue_draw()

        if in_game:
            title = f"Playing {game_name(active)}"
            text = (f"Game profile applied to {self.watcher.active_display}." if self.watcher.focused else
                    "Alt-tabbed: desktop colours until you return to the game.")
            status = title
        elif watching:
            title, status = "Desktop profile", "Auto-Switch on"
            text = "Waiting for one of your games to start."
        else:
            title, status = "Auto-Switch is paused", "Paused"
            text = "Turn on Auto-Switch in the toolbar to switch profiles when a game starts."
        self.hero_title.set_text(title)
        self.hero_text.set_text(text)
        self.status_text.set_text(status)
        (self.status_dot.add_css_class if watching else self.status_dot.remove_css_class)("on")
        with self.watch_switch.handler_block(self.watch_handler):
            self.watch_switch.set_active(watching)
        state = (active, watching)
        if state != self.shown_state:
            self.shown_state = state
            if self.stack.get_visible_child_name() == "games":
                self.render_games()
        self.push_tray()
        return GLib.SOURCE_CONTINUE

    # ---- applying profiles ---------------------------------------------------

    def report_error(self, title, error):
        self.log(f"{title}: {error}")
        ConfirmSheet(self, title, str(error), "OK", None, cancel=False)

    def apply_desktop(self, displays=None, quiet=False):
        connected = {d.name for d in resvib.list_displays()}
        displays = [d for d in (displays or self.config["desktop"]) if d in connected]
        try:
            for display in displays:
                label_text = "Desktop" if len(self.config["desktop"]) < 2 else f"Desktop ({display})"
                resvib.apply_profile(resvib.desktop_profile(self.config, display), display, self.color, label_text,
                                     self.log)
                resvib.clear_game_state(display)
            return True
        except RuntimeError as e:
            if not quiet:
                self.report_error("Couldn't apply the desktop profile", e)
            return False

    def manual_apply(self, exe):
        """Apply a game profile on demand (Test, Play, shortcut, tray). Returns the display, or None."""
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
            self.report_error("Couldn't apply the profile", e)
            return None

    def restore_expected(self, display):
        """Put a display back to what Auto-Switch wants right now."""
        if self.watcher.running:
            self.watcher.update_config(self.config)
        else:
            self.apply_desktop([display])

    def test_detail(self):
        exes = self.detail
        display = self.manual_apply(exes[0])
        if display is None:
            return
        p = self.config["games"][exes[0]]
        KeepChangesSheet(self, f"Testing {game_name(exes[0])}",
                         f"{display} is now {p['width']} × {p['height']} at {p.get('refresh', '?')} Hz. "
                         "If the screen looks wrong, wait and it switches back.",
                         revert=lambda: self.restore_expected(display))

    def play_game(self, exes):
        profile = self.config["games"][exes[0]]
        if self.manual_apply(exes[0]) is None:
            return
        try:
            detect.launch(profile["launch"])
            self.log(f"Launching {game_name(exes[0])}")
        except (OSError, ValueError) as e:
            self.report_error("Couldn't launch the game", e)

    def toggle_watching(self, on):
        if on and not self.watcher.running:
            self.watcher.start()
        elif not on and self.watcher.running:
            self.watcher.stop(restore_desktop=True)
        self.update_status()

    def restore_everything(self):
        self.watcher.stop(restore_desktop=True)
        leftover = resvib.read_state()
        if leftover:  # profiles applied manually (Test / shortcut / tray)
            self.apply_desktop(list(leftover), quiet=True)
        self.color.reset_all_adjustments()

    def quit_app(self):
        self.restore_everything()
        self.events_thread.stop()
        self.tray.stop()
        self.quit()


def main():
    if "--action" in sys.argv:  # sent by a desktop/Hyprland keyboard shortcut
        action = sys.argv[sys.argv.index("--action") + 1]
        if not hotkeys.send(f"action {action}"):
            sys.exit(f"{APP_NAME} isn't running")
        return
    if hotkeys.send("show"):
        print(f"{APP_NAME} is already running - showing its window.")
        return
    App(start_minimized="--minimized" in sys.argv).run([sys.argv[0]])
    os._exit(0)  # everything was restored in quit_app(); don't wait on helper threads


if __name__ == "__main__":
    main()
