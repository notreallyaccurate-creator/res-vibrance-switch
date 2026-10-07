"""Popular FPS games that run on Linux, the process names they run as, and their Steam app ids.

exes   - every executable the game may run under: native Linux binaries ("cs2") and Windows exes
         that run through Proton ("Overwatch.exe")
steam  - Steam app id (install detection via Steam libraries, launch via steam://)

Only games whose anti-cheat allows Linux are listed - check https://areweanticheatyet.com before adding one.
Left out because their anti-cheat blocks Linux: Valorant, Call of Duty, Apex Legends, Fortnite,
Rainbow Six Siege, PUBG, Battlefield 6 / 2042, Escape from Tarkov, Rust, Destiny 2.
"""

GAMES = {
    "Counter-Strike 2": {"exes": ["cs2"], "steam": 730},
    "Overwatch 2": {"exes": ["Overwatch.exe"], "steam": 2357570},
    "The Finals": {"exes": ["Discovery.exe"], "steam": 2073850},
    "Marvel Rivals": {"exes": ["Marvel-Win64-Shipping.exe"], "steam": 2767030},
    "Arc Raiders": {"exes": ["PioneerGame.exe"], "steam": 1808500},
    "Deadlock": {"exes": ["project8.exe"], "steam": 1422450},
    "Team Fortress 2": {"exes": ["tf_linux64"], "steam": 440},
    "Halo Infinite": {"exes": ["HaloInfinite.exe"], "steam": 1240440},
    "Hunt: Showdown 1896": {"exes": ["HuntGame.exe"], "steam": 594650},
    "Quake Champions": {"exes": ["QuakeChampions.exe"], "steam": 611500},
}

# exe (lowercase) -> game name, for labelling games added by exe name
NAME_BY_EXE = {exe.lower(): name for name, game in GAMES.items() for exe in game["exes"]}
