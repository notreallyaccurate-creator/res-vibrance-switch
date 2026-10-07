"""Finds which preset games are installed in Steam and launches games."""

import re
import shlex
import subprocess
from pathlib import Path

import presets

HOME = Path.home()
STEAM_ROOTS = [HOME / ".local/share/Steam", HOME / ".steam/steam",
               HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
               HOME / "snap/steam/common/.local/share/Steam"]


def steam_installed_appids():
    libraries = {root.resolve() for root in STEAM_ROOTS if root.is_dir()}
    for root in list(libraries):
        try:
            vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
            libraries |= {Path(p) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)}
        except OSError:
            pass
    ids = set()
    for library in libraries:
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            match = re.search(r"(\d+)", manifest.name)
            if match:
                ids.add(int(match.group(1)))
    return ids


def scan():
    """Installed preset games as {name: {"source": str, "launch": str | None}}."""
    # ponytail: Steam only. Heroic/Lutris/Bottles games can still be added with + Custom.
    appids = steam_installed_appids()
    return {name: {"source": "Steam", "launch": f"steam://rungameid/{game['steam']}"}
            for name, game in presets.GAMES.items() if game.get("steam") in appids}


def launch(command):
    """Start a game from a URI (steam://...), an executable path, or a full command line."""
    command = command.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", command, re.I):
        subprocess.Popen(["xdg-open", command], start_new_session=True)
    elif Path(command.strip('"')).is_file():
        exe = Path(command.strip('"'))
        subprocess.Popen([str(exe)], cwd=str(exe.parent), start_new_session=True)
    else:
        subprocess.Popen(shlex.split(command), start_new_session=True)
