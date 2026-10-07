"""Res & Vibrance Switch core: displays, resolution, colour, process watching and config (Linux).

Usage:
    python resvib.py run              watch for games and switch automatically
    python resvib.py apply <profile>  apply a profile once ("desktop" or a game exe)
    python resvib.py status           show displays with their resolution and vibrance
    python resvib.py modes            list resolutions supported by the main display
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ResVibranceSwitch"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"


def _run(*cmd, env=None):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                             env={**os.environ, **env} if env else None)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"{cmd[0]}: {e}") from e
    if out.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout


_hypr_lua = None


def hypr(lua, legacy):
    """Run a Lua snippet on Hyprland's Lua config (0.56+), else the `hyprctl keyword` equivalent (legacy)."""
    global _hypr_lua
    if _hypr_lua is None:
        try:
            _hypr_lua = _run("hyprctl", "eval", "local _ = 1").strip() == "ok"
        except RuntimeError:
            _hypr_lua = False
    out = (_run("hyprctl", "eval", lua) if _hypr_lua else _run("hyprctl", "keyword", *legacy)).strip()
    if out != "ok":
        raise RuntimeError(f"Hyprland: {out}")


def lua_str(text):
    return json.dumps(text)  # a JSON string is a valid Lua string literal for anything we pass


def session():
    """Which display backend this desktop needs: hyprland, kde, gnome, wlroots or x11."""
    env = os.environ
    desktop = env.get("XDG_CURRENT_DESKTOP", "").lower()
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if "kde" in desktop and shutil.which("kscreen-doctor"):
        return "kde"
    if env.get("WAYLAND_DISPLAY"):
        if "gnome" in desktop:
            return "gnome"
        if shutil.which("wlr-randr"):
            return "wlroots"
        raise RuntimeError("Unsupported Wayland desktop - install wlr-randr (sway, river, niri, labwc...)")
    if env.get("DISPLAY") and shutil.which("xrandr"):
        return "x11"
    raise RuntimeError("No supported display server found (need Hyprland, KDE, GNOME, wlr-randr or xrandr)")


SESSION = session()


# --------------------------------------------------------------------------
# Displays: each backend returns outputs as
#   {"name", "friendly", "primary", "mode": (w, h, hz), "modes": [(w, h, hz)], ...backend data}
# with exact float refresh rates; the rest of the app works with rounded ints.
# --------------------------------------------------------------------------

class Hyprland:
    def outputs(self):
        monitors = [m for m in json.loads(_run("hyprctl", "-j", "monitors", "all")) if not m.get("disabled")]
        # No "primary" in Hyprland: like Windows, call the monitor at 0,0 the main one.
        first = min(monitors, key=lambda m: (m["x"] != 0 or m["y"] != 0, m["id"]), default={}).get("id")
        return [{"name": m["name"], "friendly": m.get("model") or "", "primary": m["id"] == first,
                 "mode": (m["width"], m["height"], m["refreshRate"]),
                 "modes": [(int(w), int(h), float(hz)) for w, h, hz in
                           (re.findall(r"(\d+)x(\d+)@([\d.]+)", s)[0] for s in m.get("availableModes", []))],
                 "raw": m} for m in monitors]

    def set_mode(self, out, mode):
        m = out["raw"]
        spec = {"output": m["name"], "mode": f"{mode[0]}x{mode[1]}@{mode[2]:.3f}",
                "position": f"{m['x']}x{m['y']}", "scale": m["scale"]}
        # A runtime monitor rule replaces the whole config rule, so carry over what we can see.
        if m.get("transform"):
            spec["transform"] = m["transform"]
        if m.get("vrr"):
            spec["vrr"] = 1
        if "2101010" in m.get("currentFormat", ""):
            spec["bitdepth"] = 10
        lua = ", ".join(f"{k} = {lua_str(v) if isinstance(v, str) else v}" for k, v in spec.items())
        legacy = ",".join([spec.pop("output"), spec.pop("mode"), spec.pop("position"), str(spec.pop("scale")),
                           *(f"{k},{v}" for k, v in spec.items())])
        hypr(f"hl.monitor({{{lua}}})", ["monitor", legacy])


class KDE:
    def outputs(self):
        result = []
        for o in json.loads(_run("kscreen-doctor", "-j"))["outputs"]:
            if not (o.get("enabled") and o.get("connected")):
                continue
            ids = {(m["size"]["width"], m["size"]["height"], m["refreshRate"]): m["id"] for m in o["modes"]}
            current = next(mode for mode, mode_id in ids.items() if mode_id == o["currentModeId"])
            result.append({"name": o["name"], "friendly": o.get("model") or "",
                           "primary": o.get("priority") == 1 or o.get("primary", False),
                           "mode": current, "modes": list(ids), "ids": ids})
        return result

    def set_mode(self, out, mode):
        _run("kscreen-doctor", f"output.{out['name']}.mode.{out['ids'][mode]}")


class Gnome:
    """Mutter's DisplayConfig D-Bus API (needs PyGObject, which every GNOME install has)."""

    def _call(self, method, args=None):
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                             "org.gnome.Mutter.DisplayConfig", method, args, None, 0, -1, None).unpack()

    def outputs(self):
        _serial, monitors, logical, _props = self._call("GetCurrentState")
        primary = {spec[0] for lm in logical if lm[4] for spec in lm[5]}
        result = []
        for (connector, _vendor, product, _sn), modes, props in monitors:
            ids = {(m[1], m[2], m[3]): m[0] for m in modes}
            current = next(((m[1], m[2], m[3]) for m in modes if m[6].get("is-current")), None)
            if current:
                result.append({"name": connector, "friendly": props.get("display-name", product),
                               "primary": connector in primary, "mode": current, "modes": list(ids), "ids": ids})
        return result

    def set_mode(self, out, mode):
        from gi.repository import GLib
        serial, monitors, logical, _props = self._call("GetCurrentState")
        current = {mon[0][0]: next(m for m in mon[1] if m[6].get("is-current")) for mon in monitors
                   if any(m[6].get("is-current") for m in mon[1])}
        target = next(m for m in next(mon for mon in monitors if mon[0][0] == out["name"])[1]
                      if m[0] == out["ids"][mode])
        config = []
        for x, y, scale, transform, is_primary, mons, _ in logical:
            specs = []
            for connector, *_ in mons:
                mode_info = target if connector == out["name"] else current[connector]
                specs.append((connector, mode_info[0], {}))
                if connector == out["name"] and not any(abs(scale - s) < 0.01 for s in target[5]):
                    scale = 1.0  # old scale isn't valid for the new mode
            config.append((x, y, scale, transform, is_primary, specs))
        # ponytail: keeps positions as-is; Mutter rejects layouts that leave gaps between monitors,
        # so changing the left monitor's width in a multi-monitor setup may fail. Re-flow x/y if that bites.
        self._call("ApplyMonitorsConfig", GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})",
                                                       (serial, 1, config, {})))  # 1 = temporary


class Wlroots:
    def outputs(self):
        result = []
        for i, o in enumerate(o for o in json.loads(_run("wlr-randr", "--json")) if o.get("enabled")):
            modes = [(m["width"], m["height"], m["refresh"]) for m in o["modes"]]
            current = next((m["width"], m["height"], m["refresh"]) for m in o["modes"] if m.get("current"))
            result.append({"name": o["name"], "friendly": o.get("model") or "", "primary": i == 0,
                           "mode": current, "modes": modes})
        return result

    def set_mode(self, out, mode):
        _run("wlr-randr", "--output", out["name"], "--mode", f"{mode[0]}x{mode[1]}@{mode[2]:.3f}Hz")


class X11:
    def outputs(self):
        result, out = [], None
        for line in _run("xrandr", "--query").splitlines():
            head = re.match(r"(\S+) connected (primary )?\d+x\d+\+", line)
            if head:
                out = {"name": head[1], "friendly": "", "primary": bool(head[2]), "mode": None, "modes": []}
                result.append(out)
            elif not line.startswith(" "):
                out = None  # disconnected or disabled output
            elif out and (mode := re.match(r"\s+(\d+)x(\d+)\s+(.*)", line)):
                for rate, star in re.findall(r"([\d.]+)(\*?)\+?", mode[3]):
                    m = (int(mode[1]), int(mode[2]), float(rate))
                    out["modes"].append(m)
                    if star:
                        out["mode"] = m
        if result and not any(o["primary"] for o in result):
            result[0]["primary"] = True
        return [o for o in result if o["mode"]]

    def set_mode(self, out, mode):
        _run("xrandr", "--output", out["name"], "--mode", f"{mode[0]}x{mode[1]}", "--rate", f"{mode[2]:.2f}")


BACKEND = {"hyprland": Hyprland, "kde": KDE, "gnome": Gnome, "wlroots": Wlroots, "x11": X11}[SESSION]()


class Display:
    def __init__(self, name, friendly, primary):
        self.name = name
        self.friendly = friendly
        self.primary = primary

    @property
    def label(self):
        text = self.name
        if self.friendly:
            text += f"  ·  {self.friendly}"
        return text + ("  (main)" if self.primary else "")


def _outputs():
    return sorted(BACKEND.outputs(), key=lambda o: not o["primary"])


def _output(display):
    out = next((o for o in _outputs() if o["name"] == display), None)
    if not out:
        raise RuntimeError(f"Display {display} isn't connected")
    return out


def list_displays():
    """Displays attached to the desktop, main display first."""
    return [Display(o["name"], o["friendly"], o["primary"]) for o in _outputs()]


def primary_display_name():
    displays = list_displays()
    if not displays:
        raise RuntimeError("No display found")
    return displays[0].name


def resolve_display(name):
    """The named display if it's connected, otherwise the main display."""
    displays = list_displays()
    if name and any(d.name == name for d in displays):
        return name
    if not displays:
        raise RuntimeError("No display found")
    return displays[0].name


def get_mode(display):
    w, h, hz = _output(display)["mode"]
    return w, h, round(hz)


def current_modes():
    """{display: (w, h, hz)} for every connected display, in one backend call."""
    return {o["name"]: (o["mode"][0], o["mode"][1], round(o["mode"][2])) for o in _outputs()}


def list_modes(display):
    return sorted({(w, h, round(hz)) for w, h, hz in _output(display)["modes"]}, reverse=True)


def set_mode(display, width, height, refresh=None):
    out = _output(display)
    matches = [m for m in out["modes"] if m[:2] == (width, height) and (not refresh or round(m[2]) == refresh)]
    if not matches:
        raise RuntimeError(f"Could not set {width}x{height}@{refresh or 'any'}Hz: mode not supported")
    BACKEND.set_mode(out, max(matches, key=lambda m: m[2]))


# --------------------------------------------------------------------------
# Digital vibrance backends
# --------------------------------------------------------------------------

def _nvidia_connectors():
    """Connected DRM connectors on NVIDIA cards, e.g. ['DP-3', 'HDMI-A-1']."""
    names = []
    for card in Path("/sys/class/drm").glob("card[0-9]"):
        try:
            if (card / "device" / "vendor").read_text().strip() != "0x10de":
                continue
        except OSError:
            continue
        for conn in Path("/sys/class/drm").glob(f"{card.name}-*"):
            try:
                if (conn / "status").read_text().strip() == "connected":
                    names.append(conn.name.split("-", 1)[1])
            except OSError:
                continue
    return sorted(names, key=lambda n: (n.rsplit("-", 1)[0], int(n.rsplit("-", 1)[1])))


class Nvibrant:
    """Real NVIDIA Digital Vibrance on Wayland and X11 via https://github.com/Tremeschin/nvibrant.

    nvibrant takes one value per NVIDIA driver port and can't read values back, so we remember them.
    """

    NAME = "NVIDIA"
    TYPES = {"HDMI": "HDMI-A", "DP": "DP", "DVID": "DVI-D", "DVII": "DVI-I", "USBC": "DP"}

    def __init__(self):
        if not shutil.which("nvibrant"):
            raise RuntimeError("nvibrant not installed")
        # Probing sets every port, so this also resets vibrance to the driver default.
        lines = re.findall(r"\((\d+),\s*(\w+)\s*\).*•\s*(\w+)\s*$", _run("nvibrant"), re.M)
        self.ports = len(lines)
        # ponytail: driver port order != DRM connector numbers, so pair the connected ports of each type
        # with the connected NVIDIA connectors of that type in order. Wrong only with 2+ same-type monitors
        # wired out of order; an override map in config would fix that if anyone hits it.
        connected = _nvidia_connectors()
        self.port_of = {}
        for kind in set(self.TYPES.values()):
            ports = [int(p) for p, t, status in lines if self.TYPES.get(t) == kind and status == "Success"]
            names = [n for n in connected if n.rsplit("-", 1)[0] == kind]
            self.port_of.update(zip(names, ports))
        if not self.port_of:
            raise RuntimeError("no NVIDIA-driven display found")
        self.levels = [0] * self.ports

    def owns(self, display):
        return display in self.port_of

    # The NVIDIA Control Panel shows vibrance as 50%-100%; the driver level runs 0 (default) to 1023 (max).
    def get_vibrance_percent(self, display):
        return round(50 + self.levels[self.port_of[display]] * 50 / 1023)

    def set_vibrance_percent(self, display, percent):
        self.levels[self.port_of[display]] = round((max(50, min(100, percent)) - 50) * 1023 / 50)
        _run("nvibrant", *map(str, self.levels))


class NvidiaSettings:
    """Digital Vibrance through nvidia-settings (X11 only)."""

    NAME = "NVIDIA"

    def __init__(self):
        if SESSION != "x11" or not shutil.which("nvidia-settings"):
            raise RuntimeError("nvidia-settings needs an X11 session")

    def owns(self, display):
        try:
            self.get_vibrance_percent(display)
            return True
        except (RuntimeError, ValueError):
            return False

    def get_vibrance_percent(self, display):
        level = int(_run("nvidia-settings", "-t", "-q", f"[DPY:{display}]/DigitalVibrance").strip())
        return round(50 + max(0, level) * 50 / 1023)

    def set_vibrance_percent(self, display, percent):
        level = round((max(50, min(100, percent)) - 50) * 1023 / 50)
        _run("nvidia-settings", "-a", f"[DPY:{display}]/DigitalVibrance={level}")


SHADER = """#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

void main() {{
    vec4 c = texture(tex, v_texcoord);
    vec3 v = mix(vec3(dot(c.rgb, vec3(0.2126, 0.7152, 0.0722))), c.rgb, {saturation:.4f});
    v = pow(clamp(v, 0.0, 1.0), vec3({inv_gamma:.4f}));
    v = (v - 0.5) * {contrast:.4f} + 0.5 + {brightness:.4f};
    fragColor = vec4(clamp(v, 0.0, 1.0), c.a);
}}
"""


class HyprShader:
    """Saturation + brightness/contrast/gamma as a Hyprland screen shader. Works on any GPU.

    ponytail: Hyprland's screen shader is global, so the last applied values cover every monitor.
    """

    NAME = "Hyprland shader"
    MAX_SATURATION = 2.0  # what 100% vibrance means; NVIDIA's max is roughly 2x. Calibration knob.

    def __init__(self):
        if SESSION != "hyprland":
            raise RuntimeError("needs Hyprland")
        self.vibrance = 50
        self.adjustment = None  # (brightness, contrast, gamma) or None
        self._flip = False

    def owns(self, display):
        return True

    def get_vibrance_percent(self, display):
        return self.vibrance

    def set_vibrance_percent(self, display, percent):
        self.vibrance = max(50, min(100, percent))
        self._apply()

    def set_adjustment(self, brightness, contrast, gamma):
        self.adjustment = (brightness, contrast, gamma)
        self._apply()

    def reset_adjustment(self):
        self.adjustment = None
        self._apply()

    @staticmethod
    def _set_shader(path):
        hypr(f"hl.config({{decoration = {{screen_shader = {lua_str(path)}}}}})",
             ["decoration:screen_shader", path or "[[EMPTY]]"])

    def _apply(self):
        brightness, contrast, gamma = self.adjustment or (50, 50, 1.0)
        if self.vibrance == 50 and not self.adjustment:
            self._set_shader("")  # off = direct scanout stays possible
            return
        # Hyprland only reloads the shader when the path changes, so alternate between two files.
        self._flip = not self._flip
        path = DATA_DIR / f"screen-{'ab'[self._flip]}.frag"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SHADER.format(saturation=1 + (self.vibrance - 50) / 50 * (self.MAX_SATURATION - 1),
                                      inv_gamma=1 / gamma, contrast=contrast / 50,
                                      brightness=(brightness - 50) / 100))
        self._set_shader(str(path))


# --------------------------------------------------------------------------
# Colour engine: vibrance backends + brightness / contrast / gamma
# --------------------------------------------------------------------------

NEUTRAL_COLOR = {"brightness": 50, "contrast": 50, "gamma": 1.0}


def color_adjusted(profile):
    return any(abs(profile.get(k, v) - v) > 1e-6 for k, v in NEUTRAL_COLOR.items())


class Color:
    def __init__(self):
        self.backends = []
        self.errors = []
        for backend in (Nvibrant, NvidiaSettings, HyprShader):
            try:
                self.backends.append(backend())
            except Exception as e:  # missing tool, wrong session, unexpected output...
                self.errors.append(f"{backend.NAME}: {e}")
        self.shader = next((b for b in self.backends if isinstance(b, HyprShader)), None)
        self._owners = {}

    def _backend(self, display):
        if display not in self._owners:
            self._owners[display] = next((b for b in self.backends if b.owns(display)), None)
        return self._owners[display]

    def vendor(self, display):
        backend = self._backend(display)
        return backend.NAME if backend else None

    def supports_vibrance(self, display):
        return self._backend(display) is not None

    def get_vibrance(self, display):
        backend = self._backend(display)
        return backend.get_vibrance_percent(display) if backend else None

    def set_vibrance(self, display, percent):
        backend = self._backend(display)
        if backend:
            backend.set_vibrance_percent(display, percent)

    @staticmethod
    def vibrance_hint():
        return ("Digital vibrance needs Hyprland (any GPU), or an NVIDIA card with nvibrant installed, "
                "or nvidia-settings on X11.")

    def supports_adjustment(self, display):
        return self.shader is not None

    def adjustment_unavailable_reason(self, display):
        if self.supports_adjustment(display):
            return None
        return "Unavailable on this desktop: brightness/contrast/gamma need Hyprland."

    def set_adjustment(self, display, brightness, contrast, gamma):
        self.shader.set_adjustment(brightness, contrast, gamma)

    def reset_adjustment(self, display):
        if self.shader and self.shader.adjustment:
            self.shader.reset_adjustment()

    def reset_all_adjustments(self):
        try:
            self.reset_adjustment(None)
        except RuntimeError:
            pass


# --------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------

def process_name(pid):
    """Lowercase executable name: 'cs2' for native games, 'overwatch.exe' for Proton/Wine games."""
    try:
        argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="ignore")
        name = re.split(r"[\\/]", argv0)[-1]
        return (name or Path(f"/proc/{pid}/comm").read_text().strip()).lower()
    except OSError:
        return None


def running_processes():
    """Map of pid -> lowercase exe name for every running process."""
    procs = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit() and (name := process_name(entry)):
            procs[int(entry)] = name
    return procs


def running_exes():
    return set(running_processes().values())


def _x11_window_pid(window):
    out = _run("xprop", "-id", window, "_NET_WM_PID")
    return int(out.split("=")[1]) if "=" in out else None


def window_pids():
    """Pids of apps with an open window, where the desktop lets us see them (else empty)."""
    try:
        if SESSION == "hyprland":
            return {c["pid"] for c in json.loads(_run("hyprctl", "-j", "clients")) if c.get("mapped")}
        if SESSION == "x11" and shutil.which("xprop"):
            ids = re.findall(r"0x[0-9a-f]+", _run("xprop", "-root", "_NET_CLIENT_LIST"))
            return {pid for w in ids if (pid := _x11_window_pid(w))}
    except (RuntimeError, ValueError):
        pass
    return set()


FOCUS_SUPPORTED = SESSION == "hyprland" or (SESSION == "x11" and bool(shutil.which("xprop")))


def foreground_exe():
    """Lowercase exe name of the focused app, or None when the desktop doesn't tell us."""
    # ponytail: KDE/GNOME Wayland need a KWin script / shell extension to expose the focused window;
    # without it alt-tab detection is off there and the game profile simply stays on.
    try:
        if SESSION == "hyprland":
            pid = json.loads(_run("hyprctl", "-j", "activewindow")).get("pid")
        elif SESSION == "x11" and shutil.which("xprop"):
            window = re.search(r"0x[0-9a-f]+", _run("xprop", "-root", "_NET_ACTIVE_WINDOW"))
            pid = window and _x11_window_pid(window[0])
        else:
            return None
    except (RuntimeError, ValueError):
        return None
    return process_name(pid) if pid else None


# --------------------------------------------------------------------------
# Config & crash-recovery state
# --------------------------------------------------------------------------

DEFAULT_HOTKEYS = {"toggle_watch": "ctrl+alt+p", "desktop": "ctrl+alt+d", "show": ""}
DEFAULT_SETTINGS = {"poll_seconds": 2, "auto_watch": True, "theme": "dark", "notifications": True,
                    "check_updates": True}


def default_config():
    desktop = {}
    for d in list_displays():
        width, height, refresh = get_mode(d.name)
        desktop[d.name] = {"width": width, "height": height, "refresh": refresh}
        if d.primary:
            desktop[d.name]["vibrance"] = 50
    return {"version": 2, "setup_complete": False, **DEFAULT_SETTINGS, "desktop": desktop, "games": {},
            "hotkeys": dict(DEFAULT_HOTKEYS)}


def migrate(config):
    for key, value in DEFAULT_SETTINGS.items():
        config.setdefault(key, value)
    config.setdefault("setup_complete", True)
    config.setdefault("desktop", {})
    config.setdefault("hotkeys", dict(DEFAULT_HOTKEYS))
    config["games"] = {exe.lower(): p for exe, p in config.get("games", {}).items()}
    for d in list_displays():
        if d.name not in config["desktop"]:
            width, height, refresh = get_mode(d.name)
            config["desktop"][d.name] = {"width": width, "height": height, "refresh": refresh}
    config["version"] = 2
    return config


def load_config():
    if not CONFIG_PATH.exists():
        save_config(default_config())
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return migrate(json.load(f))


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def desktop_profile(config, display):
    if display not in config["desktop"]:
        width, height, refresh = get_mode(display)
        config["desktop"][display] = {"width": width, "height": height, "refresh": refresh}
    return config["desktop"][display]


def read_state():
    """Displays currently switched to a game profile: {display: exe}. Survives crashes."""
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state):
    try:
        if state:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        else:
            STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def mark_game_state(display, exe):
    state = read_state()
    state[display] = exe
    _write_state(state)


def clear_game_state(display):
    state = read_state()
    if state.pop(display, None) is not None:
        _write_state(state)


# --------------------------------------------------------------------------
# Applying profiles
# --------------------------------------------------------------------------

_apply_lock = threading.RLock()


def log_line(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def describe(profile):
    text = f"{profile['width']}x{profile['height']}"
    if profile.get("refresh"):
        text += f" @ {profile['refresh']}Hz"
    if "vibrance" in profile:
        text += f", vibrance {profile['vibrance']}%"
    if color_adjusted(profile):
        text += (f", brightness {profile.get('brightness', 50)} / contrast {profile.get('contrast', 50)}"
                 f" / gamma {profile.get('gamma', 1.0):.2f}")
    return text


def apply_mode(profile, display):
    width, height, refresh = profile["width"], profile["height"], profile.get("refresh")
    cur_w, cur_h, cur_hz = get_mode(display)
    if (width, height) != (cur_w, cur_h) or (refresh and refresh != cur_hz):
        set_mode(display, width, height, refresh)


def apply_color(profile, display, color):
    with _apply_lock:
        if "vibrance" in profile:
            color.set_vibrance(display, profile["vibrance"])
        if color_adjusted(profile) and color.supports_adjustment(display):
            color.set_adjustment(display, *(profile.get(k, v) for k, v in NEUTRAL_COLOR.items()))
        else:
            color.reset_adjustment(display)


def apply_profile(profile, display, color, label, log=log_line):
    with _apply_lock:
        apply_mode(profile, display)
        apply_color(profile, display, color)
    log(f"{label}: {describe(profile)}")


# --------------------------------------------------------------------------
# Watcher
# --------------------------------------------------------------------------

class Watcher:
    """Background thread that applies the matching profile as games start, stop, gain or lose focus."""

    def __init__(self, config, color, log=log_line, namer=None, on_switch=None):
        self.config = config
        self.color = color
        self.log = log
        self.namer = namer or (lambda exe: exe)
        self.on_switch = on_switch or (lambda event, exe: None)
        self.active = None          # running game's exe, "desktop", or None before the first pass
        self.active_display = None  # display the active game profile is on
        self.focused = True         # whether the active game is the focused window
        self.wake = threading.Event()
        self._force = False
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def in_game(self):
        return self.active not in (None, "desktop")

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.active = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def stop(self, restore_desktop=True):
        self._stop.set()
        self.wake.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if restore_desktop and self.in_game:
            self._safe(self._restore_desktop, self.active_display)
        self.active = self.active_display = None

    def update_config(self, config):
        """Swap in an edited config and re-apply the current profile with the new values."""
        self.config = config
        self._force = True
        self.wake.set()

    def poke(self):
        """Re-check right away (called when the focused window changes)."""
        self.wake.set()

    def _safe(self, fn, *args):
        try:
            fn(*args)
            return True
        except RuntimeError as e:
            self.log(f"Error: {e}")
            return False

    def _restore_desktop(self, display):
        label = "Desktop" if len(self.config["desktop"]) < 2 else f"Desktop ({display})"
        apply_profile(desktop_profile(self.config, display), display, self.color, label, self.log)
        clear_game_state(display)

    def _apply_game(self, exe):
        profile = self.config["games"][exe]
        display = resolve_display(profile.get("display"))
        if self.in_game and self.active_display != display:
            self._restore_desktop(self.active_display)
        desktop = desktop_profile(self.config, display)
        if "vibrance" not in desktop and self.color.supports_vibrance(display):
            desktop["vibrance"] = self.color.get_vibrance(display)  # remember what to come back to
        mark_game_state(display, exe)
        self.active, self.active_display, self.focused = exe, display, True
        apply_profile(profile, display, self.color, self.namer(exe), self.log)

    def _apply_desktop(self):
        try:
            if self.in_game:
                self._restore_desktop(self.active_display)
            else:
                connected = {d.name for d in list_displays()}
                for display in [d for d in self.config["desktop"] if d in connected]:
                    self._restore_desktop(display)
        finally:
            self.active, self.active_display = "desktop", None

    def _check_focus(self, exe):
        profile = self.config["games"].get(exe)
        if profile is None:
            return
        foreground = foreground_exe() if profile.get("alt_tab", True) else None
        focused = foreground is None or foreground == exe  # unknown focus counts as in-game
        if focused == self.focused:
            return
        self.focused = focused
        display = self.active_display
        if focused:
            apply_color(profile, display, self.color)
            self.log(f"{self.namer(exe)}: back in game")
            self.on_switch("focus_in", exe)
        else:
            apply_color(desktop_profile(self.config, display), display, self.color)
            self.log(f"{self.namer(exe)}: alt-tabbed - desktop colours until you return")
            self.on_switch("focus_out", exe)

    def _loop(self):
        self.log(f"Watching for {len(self.config['games'])} game(s)")
        while not self._stop.is_set():
            config = self.config
            running = running_exes()
            target = next((exe for exe in config["games"] if exe in running), "desktop")
            if target != self.active or self._force:
                first = self.active is None
                self._force = False
                if target == "desktop":
                    self._safe(self._apply_desktop)
                else:
                    self._safe(self._apply_game, target)
                self.on_switch("startup" if first and target == "desktop" else
                               "desktop" if target == "desktop" else "game", target)
            elif target != "desktop":
                self._safe(self._check_focus, target)
            self.wake.wait(config.get("poll_seconds", 2))
            self.wake.clear()
        self.log("Stopped watching")


def watch(config, color):
    watcher = Watcher(config, color)
    watcher.start()
    print("Ctrl+C to stop")
    try:
        while watcher.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "modes":
        for w, h, hz in list_modes(primary_display_name()):
            print(f"{w}x{h} @ {hz}Hz")
        return
    color = Color()
    if cmd == "status":
        print(f"Session: {SESSION}")
        for d in list_displays():
            w, h, hz = get_mode(d.name)
            vib = color.get_vibrance(d.name)
            print(f"{d.label}\n  {w}x{h} @ {hz}Hz, vibrance "
                  f"{f'{vib}% ({color.vendor(d.name)})' if vib is not None else 'not supported'}")
        for error in color.errors:
            print(f"  ({error})")
        return
    config = load_config()
    if cmd == "run":
        watch(config, color)
    elif cmd == "apply" and len(args) == 2:
        name = args[1].lower()
        if name == "desktop":
            for display in config["desktop"]:
                apply_profile(config["desktop"][display], display, color, f"desktop {display}")
        elif name in config["games"]:
            profile = config["games"][name]
            apply_profile(profile, resolve_display(profile.get("display")), color, name)
        else:
            raise SystemExit(f"No profile named '{args[1]}'")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
