"""Finds which preset games are installed (Steam, Riot, Epic, installed programs) and launches games."""

import json
import os
import re
import subprocess
import winreg
from pathlib import Path

import presets

UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _norm(text):
    return re.sub(r"[\u2122\u00ae\u00a9]", "", text).lower().strip()


def steam_root():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return None


def steam_installed_appids():
    root = steam_root()
    if not root:
        return set()
    libraries = {root}
    try:
        vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
        libraries |= {Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)}
    except OSError:
        pass
    ids = set()
    for library in libraries:
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            match = re.search(r"(\d+)", manifest.name)
            if match:
                ids.add(int(match.group(1)))
    return ids


def installed_program_names():
    names = set()
    for hive, path in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(hive, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            names.add(_norm(winreg.QueryValueEx(sub, "DisplayName")[0]))
                    except OSError:
                        continue
        except OSError:
            continue
    return names


def epic_manifests():
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    manifests = []
    for item in folder.glob("*.item"):
        try:
            manifests.append(json.loads(item.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return manifests


def riot_client():
    path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Riot Games" / "RiotClientInstalls.json"
    try:
        client = json.loads(path.read_text(encoding="utf-8")).get("rc_default")
        return client if client and Path(client).exists() else None
    except (OSError, ValueError):
        return None


def scan():
    """Installed preset games as {name: {"source": str, "launch": str | None}}."""
    appids = steam_installed_appids()
    programs = installed_program_names()
    epic = epic_manifests()
    riot = riot_client()
    found = {}
    for name, game in presets.GAMES.items():
        keywords = game.get("match", [])
        if game.get("steam") in appids:
            found[name] = {"source": "Steam", "launch": f"steam://rungameid/{game['steam']}"}
        elif game.get("riot") and riot and any(k in p for k in keywords for p in programs):
            found[name] = {"source": "Riot Games",
                           "launch": f'"{riot}" --launch-product={game["riot"]} --launch-patchline=live'}
        elif game.get("epic") and (m := next((m for m in epic
                                               if any(k in _norm(m.get("DisplayName", "")) for k in keywords)), None)):
            uri = (f"com.epicgames.launcher://apps/{m.get('CatalogNamespace')}%3A{m.get('CatalogItemId')}"
                   f"%3A{m.get('AppName')}?action=launch&silent=true")
            found[name] = {"source": "Epic Games", "launch": uri}
        elif keywords and any(k in p for k in keywords for p in programs):
            found[name] = {"source": "Installed", "launch": None}
    return found


def launch(command):
    """Start a game from a URI (steam://, com.epicgames...), an exe path, or a full command line."""
    command = command.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", command, re.I):
        os.startfile(command)
    elif Path(command.strip('"')).is_file():
        exe = Path(command.strip('"'))
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
    else:
        subprocess.Popen(command, close_fds=True)
