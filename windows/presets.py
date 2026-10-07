"""Popular FPS games: the process names they run as, plus how to detect and launch them.

exes   - every executable the game may run under (DX11/DX12/Vulkan builds etc.)
steam  - Steam app id (install detection via Steam libraries, launch via steam://)
riot   - Riot Client product id
epic   - marks an Epic Games Store title (detected from Epic's install manifests)
match  - lowercase name fragments matched against installed-program names
"""

GAMES = {
    "Counter-Strike 2": {"exes": ["cs2.exe"], "steam": 730, "match": ["counter-strike 2"]},
    "Counter-Strike: GO (legacy)": {"exes": ["csgo.exe"], "match": ["counter-strike: global offensive"]},
    "Valorant": {"exes": ["VALORANT-Win64-Shipping.exe"], "riot": "valorant", "match": ["valorant"]},
    "Call of Duty (BO6 / MW3 / Warzone)": {"exes": ["cod.exe"], "steam": 1938090, "match": ["call of duty"]},
    "Call of Duty: Modern Warfare (2019)": {"exes": ["ModernWarfare.exe"],
                                            "match": ["call of duty: modern warfare", "call of duty modern warfare"]},
    "Call of Duty: Black Ops Cold War": {"exes": ["BlackOpsColdWar.exe"], "match": ["black ops cold war"]},
    "Apex Legends": {"exes": ["r5apex.exe", "r5apex_dx12.exe"], "steam": 1172470, "match": ["apex legends"]},
    "Fortnite": {"exes": ["FortniteClient-Win64-Shipping.exe"], "epic": True, "match": ["fortnite"]},
    "Overwatch 2": {"exes": ["Overwatch.exe"], "steam": 2357570, "match": ["overwatch"]},
    "Rainbow Six Siege": {"exes": ["RainbowSix.exe", "RainbowSix_Vulkan.exe"], "steam": 359550,
                          "match": ["rainbow six siege"]},
    "PUBG: Battlegrounds": {"exes": ["TslGame.exe"], "steam": 578080, "match": ["pubg"]},
    "Battlefield 6": {"exes": ["bf6.exe"], "steam": 2807960, "match": ["battlefield 6"]},
    "Battlefield 2042": {"exes": ["BF2042.exe"], "steam": 1517290, "match": ["battlefield 2042"]},
    "The Finals": {"exes": ["Discovery.exe"], "steam": 2073850, "match": ["the finals"]},
    "Marvel Rivals": {"exes": ["Marvel-Win64-Shipping.exe"], "steam": 2767030, "match": ["marvel rivals"]},
    "Arc Raiders": {"exes": ["PioneerGame.exe"], "steam": 1808500, "match": ["arc raiders"]},
    "Escape from Tarkov": {"exes": ["EscapeFromTarkov.exe"], "match": ["escape from tarkov", "escapefromtarkov"]},
    "Rust": {"exes": ["RustClient.exe"], "steam": 252490, "match": []},
    "Team Fortress 2": {"exes": ["tf_win64.exe"], "steam": 440, "match": []},
    "Deadlock": {"exes": ["project8.exe"], "steam": 1422450, "match": []},
    "Halo Infinite": {"exes": ["HaloInfinite.exe"], "steam": 1240440, "match": ["halo infinite"]},
    "Destiny 2": {"exes": ["destiny2.exe"], "steam": 1085660, "match": ["destiny 2"]},
    "Hunt: Showdown 1896": {"exes": ["HuntGame.exe"], "steam": 594650, "match": ["hunt: showdown"]},
    "Quake Champions": {"exes": ["QuakeChampions.exe"], "steam": 611500, "match": ["quake champions"]},
}

# exe (lowercase) -> game name, for labelling games added by exe name
NAME_BY_EXE = {exe.lower(): name for name, game in GAMES.items() for exe in game["exes"]}
