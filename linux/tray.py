"""Tray icon helper, run by app.py as a separate process.

The AppIndicator tray needs GTK3, which can't share a process with the GTK4 window. The app writes state as
JSON lines on our stdin; we print menu choices on stdout ("show", "toggle", "desktop", "game:<exe>", "quit").
"""

import json
import sys
import threading

from PIL import Image, ImageDraw

APP_NAME = "Res & Vibrance Switch"
GRADIENT = [(0x22, 0xD3, 0xEE), (0x7C, 0x5C, 0xFF), (0xEC, 0x48, 0x99), (0xF5, 0x9E, 0x0B)]
DOTS = {"paused": "#8e8e93", "watching": "#34c759", "game": "#007aff"}


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
    """App icon with a status dot: grey paused, green watching, blue in a game."""
    img = make_icon_image(64)
    d = ImageDraw.Draw(img)
    d.ellipse([36, 36, 63, 63], fill=(12, 13, 18, 255))
    d.ellipse([41, 41, 58, 58], fill=DOTS[state])
    return img


def main():
    import pystray

    state = {"state": "paused", "title": APP_NAME, "watching": False, "games": []}

    def send(command):
        def run(*_):
            print(command, flush=True)
        return run

    def switch_items():
        yield pystray.MenuItem("Desktop", send("desktop"))
        for name, exe in state["games"]:
            yield pystray.MenuItem(name, send(f"game:{exe}"))

    icon = pystray.Icon("res-vibrance-switch", tray_image("paused"), APP_NAME, pystray.Menu(
        pystray.MenuItem("Open", send("show"), default=True),
        pystray.MenuItem(lambda _: "Pause Auto-Switch" if state["watching"] else "Start Auto-Switch",
                         send("toggle")),
        pystray.MenuItem("Switch To", pystray.Menu(switch_items)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", send("quit")),
    ))

    gtk_backend = "appindicator" in type(icon).__module__ or "gtk" in type(icon).__module__

    def apply(data):
        state.update(data)
        icon.icon = tray_image(state["state"])
        icon.title = state["title"]
        icon.update_menu()
        return False

    def read():
        for line in sys.stdin:
            data = json.loads(line)
            if gtk_backend:  # GTK objects must be touched from the GTK thread
                from gi.repository import GLib
                GLib.idle_add(apply, data)
            else:
                apply(data)
        icon.stop()  # stdin closed: the app is gone

    threading.Thread(target=read, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
