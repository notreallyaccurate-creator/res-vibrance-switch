"""A Win32 message-loop thread for global hotkeys and instant foreground-window notifications.

The foreground hook lets the watcher react the moment a game window appears or you alt-tab,
instead of waiting for the next poll - without needing admin rights.
"""

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_APP_RELOAD = 0x8001
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WinEventProc,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}

# Key names follow Tk's keysyms (lowercase) so captured keys can be stored directly.
KEYS = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
KEYS = {k.lower(): v for k, v in KEYS.items()}
KEYS.update({str(d): 0x30 + d for d in range(10)})
KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})
KEYS.update({f"kp_{d}": 0x60 + d for d in range(10)})
KEYS.update({
    "space": 0x20, "prior": 0x21, "next": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E, "tab": 0x09, "pause": 0x13,
    "scroll_lock": 0x91, "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "semicolon": 0xBA, "apostrophe": 0xDE, "bracketleft": 0xDB, "bracketright": 0xDD,
    "backslash": 0xDC, "grave": 0xC0,
})
LABELS = {
    "prior": "PgUp", "next": "PgDn", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "semicolon": ";", "apostrophe": "'", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "grave": "`", "scroll_lock": "ScrollLock", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
}


def parse_hotkey(text):
    """'ctrl+alt+p' -> (modifiers, virtual key), or None if invalid."""
    mods, key = 0, None
    for part in (p.strip().lower() for p in (text or "").split("+") if p.strip()):
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        else:
            key = part
    if key not in KEYS:
        return None
    return mods, KEYS[key]


def _key_label(part):
    if part in LABELS:
        return LABELS[part]
    if part.startswith("kp_"):
        return "Num " + part[3:]
    return part.upper() if len(part) <= 3 else part.capitalize()


def format_hotkey(text):
    """'ctrl+alt+prior' -> 'Ctrl + Alt + PgUp'."""
    return " + ".join(_key_label(p.strip().lower()) for p in (text or "").split("+") if p.strip())


class EventThread(threading.Thread):
    def __init__(self, on_foreground, on_hotkey, on_hotkey_error):
        super().__init__(daemon=True, name="win32-events")
        self.on_foreground = on_foreground
        self.on_hotkey = on_hotkey
        self.on_hotkey_error = on_hotkey_error
        self._hotkeys = {}
        self._ids = {}
        self._tid = None
        self._ready = threading.Event()
        self._proc = WinEventProc(self._on_event)  # keep a reference so it isn't garbage collected

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # make sure the thread has a message queue
        self._ready.set()
        hook = user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._proc,
                                      0, 0, WINEVENT_OUTOFCONTEXT)
        self._register()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in self._ids:
                self.on_hotkey(self._ids[msg.wParam])
            elif msg.message == WM_APP_RELOAD:
                self._register()
        self._unregister()
        if hook:
            user32.UnhookWinEvent(hook)

    def _on_event(self, *_):
        self.on_foreground()

    def _unregister(self):
        for hotkey_id in self._ids:
            user32.UnregisterHotKey(None, hotkey_id)
        self._ids = {}

    def _register(self):
        self._unregister()
        failed = []
        for hotkey_id, (action, text) in enumerate(sorted(self._hotkeys.items()), start=1):
            parsed = parse_hotkey(text)
            if not parsed:
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                self._ids[hotkey_id] = action
            else:
                failed.append(text)
        if failed:
            self.on_hotkey_error(failed)

    def set_hotkeys(self, mapping):
        """mapping: {action: "ctrl+alt+p"}; re-registers everything on the event thread."""
        self._hotkeys = {action: text for action, text in mapping.items() if text}
        if self._ready.is_set():
            user32.PostThreadMessageW(self._tid, WM_APP_RELOAD, 0, 0)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
