"""Global hotkeys, single-instance messaging and instant focus notifications on Linux.

The running app listens on a Unix socket; `app.py --action <name>` and a second launch talk to it.
On Hyprland, hotkeys are registered as compositor binds that run that command. Other desktops don't let
apps grab keys, so the user binds the same command in their desktop's shortcut settings.
"""

import os
import socket
import tempfile
import threading
from pathlib import Path

import resvib

SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "resvibranceswitch.sock"
CAN_REGISTER = resvib.SESSION == "hyprland"

MODIFIERS = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "SUPER"}

# Key names are X keysyms (lowercase) so captured keys can be stored directly.
KEYS = {chr(c) for c in range(ord("a"), ord("z") + 1)} | {str(d) for d in range(10)}
KEYS |= {f"f{n}" for n in range(1, 25)} | {f"kp_{d}" for d in range(10)}
KEYS |= {"space", "prior", "next", "end", "home", "left", "up", "right", "down", "insert", "delete", "tab",
         "pause", "scroll_lock", "minus", "equal", "comma", "period", "slash", "semicolon", "apostrophe",
         "bracketleft", "bracketright", "backslash", "grave"}
LABELS = {
    "prior": "PgUp", "next": "PgDn", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "semicolon": ";", "apostrophe": "'", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "grave": "`", "scroll_lock": "ScrollLock", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super",
}
# xkb spells these keysyms capitalised; the punctuation ones stay lowercase.
XKB_CAPITALISED = {"prior", "next", "end", "home", "left", "up", "right", "down", "insert", "delete", "tab",
                   "pause"}


def _parts(text):
    return [p.strip().lower() for p in (text or "").split("+") if p.strip()]


def _key_label(part):
    if part in LABELS:
        return LABELS[part]
    if part.startswith("kp_"):
        return "Num " + part[3:]
    return part.upper() if len(part) <= 3 else part.capitalize()


def format_hotkey(text):
    """'ctrl+alt+prior' -> 'Ctrl + Alt + PgUp'."""
    return " + ".join(_key_label(p) for p in _parts(text))


def _xkb_key(key):
    if len(key) == 1 or key.startswith("f"):
        return key.upper()
    if key.startswith("kp_"):
        return "KP_" + key[3:]
    if key == "scroll_lock":
        return "Scroll_Lock"
    return key.capitalize() if key in XKB_CAPITALISED else key


def hypr_keys(text):
    """'ctrl+alt+p' -> ('CTRL ALT', 'P'), or None if invalid."""
    parts = _parts(text)
    mods = [MODIFIERS[p] for p in parts if p in MODIFIERS]
    keys = [p for p in parts if p not in MODIFIERS]
    if len(keys) != 1 or keys[0] not in KEYS:
        return None
    return " ".join(mods), _xkb_key(keys[0])


def send(message):
    """Deliver a message to the running app. Returns False if no copy is running."""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect(str(SOCKET))
            s.sendall(message.encode())
        return True
    except OSError:
        return False


class EventThread:
    def __init__(self, on_foreground, on_hotkey, on_hotkey_error, on_show, command):
        self.on_foreground = on_foreground
        self.on_hotkey = on_hotkey
        self.on_hotkey_error = on_hotkey_error
        self.on_show = on_show
        self.command = command  # action -> shell command that triggers it
        self._bound = []
        self._server = None

    def start(self):
        SOCKET.unlink(missing_ok=True)
        self._server = socket.socket(socket.AF_UNIX)
        self._server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        self._inode = SOCKET.stat().st_ino
        self._server.listen()
        threading.Thread(target=self._serve, daemon=True, name="ipc").start()
        if resvib.SESSION == "hyprland":
            threading.Thread(target=self._hypr_events, daemon=True, name="hypr-events").start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return  # closed by stop()
            with conn:
                message = conn.recv(4096).decode(errors="ignore").strip()
            if message == "show":
                self.on_show()
            elif message.startswith("action "):
                self.on_hotkey(message[7:])

    def _hypr_events(self):
        """React the moment the focused window changes instead of waiting for the next poll."""
        path = (Path(os.environ["XDG_RUNTIME_DIR"]) / "hypr" / os.environ["HYPRLAND_INSTANCE_SIGNATURE"] /
                ".socket2.sock")
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(path))
                for line in s.makefile(encoding="utf-8", errors="ignore"):
                    if line.startswith("activewindow>>"):
                        self.on_foreground()
        except OSError:
            pass  # the watcher still polls

    def _unregister(self):
        for mods, key in self._bound:
            try:
                resvib.hypr(f"hl.unbind({resvib.lua_str(' + '.join(mods.split() + [key]))})",
                            ["unbind", f"{mods},{key}"])
            except RuntimeError:
                pass
        self._bound = []

    def set_hotkeys(self, mapping):
        """mapping: {action: "ctrl+alt+p"}; re-registers everything (Hyprland only)."""
        if not CAN_REGISTER:
            return
        self._unregister()
        failed = []
        for action, text in sorted(mapping.items()):
            keys = hypr_keys(text)
            if not keys:
                continue
            mods, key = keys
            cmd = self.command(action)
            combo = " + ".join(mods.split() + [key])
            try:
                resvib.hypr(f"hl.bind({resvib.lua_str(combo)}, hl.dsp.exec_cmd({resvib.lua_str(cmd)}))",
                            ["bind", f"{mods},{key},exec,{cmd}"])
                self._bound.append((mods, key))
            except RuntimeError:
                failed.append(text)
        if failed:
            self.on_hotkey_error(failed)

    def stop(self):
        self._unregister()
        if self._server:
            self._server.close()
        try:
            if SOCKET.stat().st_ino == self._inode:  # don't delete a newer instance's socket
                SOCKET.unlink()
        except (OSError, AttributeError):
            pass
