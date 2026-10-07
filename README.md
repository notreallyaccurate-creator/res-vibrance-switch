# Res & Vibrance Switch for Linux

Automatically switch your **resolution** and **digital vibrance** when a game starts, and put your desktop back when it closes.
A native GTK4 app for Linux: a sidebar window with System Settings–style controls, light and dark mode,
and a tray icon so it keeps working in the background.

## Install

```sh
git clone https://github.com/notreallyaccurate-creator/res-vibrance-switch.git
cd res-vibrance-switch
./install.sh
```

The installer handles Arch/CachyOS/Manjaro, Debian/Ubuntu/Mint/Pop!_OS, Fedora/Nobara/Bazzite and openSUSE.
It installs a few system packages, sets up a private Python environment in `~/.local/share/res-vibrance-switch`,
and adds an app-menu entry plus the `res-vibrance-switch` command.

## What works where

| Desktop | Resolution | Vibrance | Brightness / contrast / gamma | Alt-tab detection | Global hotkeys |
|---|---|---|---|---|---|
| **Hyprland** | ✅ | ✅ any GPU (screen shader), or NVIDIA via nvibrant | ✅ | ✅ instant | ✅ |
| **KDE Plasma** | ✅ `kscreen-doctor` | NVIDIA via nvibrant | – | X11 only | bind command* |
| **GNOME** | ✅ Mutter D-Bus | NVIDIA via nvibrant | – | X11 only | bind command* |
| **sway, river, niri, labwc…** | ✅ `wlr-randr` | NVIDIA via nvibrant | – | – | bind command* |
| **Any X11 desktop** (XFCE, Cinnamon, MATE, i3…) | ✅ `xrandr` | NVIDIA via nvibrant or nvidia-settings | – | ✅ | bind command* |

\*Only Hyprland lets apps register global keys. On other desktops, *Settings → Global hotkeys* shows the
commands (`res-vibrance-switch --action toggle_watch` and so on) to bind in your desktop's keyboard shortcut settings.

**NVIDIA vibrance** uses [nvibrant](https://github.com/Tremeschin/nvibrant), which `install.sh` installs automatically
on NVIDIA systems. It's real Digital Vibrance in the driver: per monitor, no performance cost.

**The Hyprland shader** works on AMD and Intel too. It covers every monitor at once, can't be seen in screenshots,
and turns off direct scanout while it's active, which adds a little latency in fullscreen games.
At 50% vibrance with neutral colours, the shader is switched off entirely.

## Stretched resolutions

The resolution list shows the modes your monitor reports. For stretched resolutions that your monitor doesn't
list (e.g. 1280×960 at 240 Hz), use gamescope in the game's Steam launch options instead:

```
gamescope -w 1280 -h 960 -W 1920 -H 1080 -r 240 -S stretch -f -- %command%
```

## Games

Native Linux games are matched by their binary (`cs2`, `tf_linux64`) and Proton games by their Windows exe
(`Overwatch.exe`). Installed Steam games are detected for you. For anything else, use **Add Custom Game**
and pick the game from the list of open apps while it's running.

The built-in list only includes games whose anti-cheat allows Linux (per [areweanticheatyet.com](https://areweanticheatyet.com)):
CS2, Overwatch 2, The Finals, Marvel Rivals, Arc Raiders, Deadlock, TF2, Halo Infinite, Hunt: Showdown 1896 and
Quake Champions. The app never touches game files or memory, only display settings.

## Settings and crash recovery

Settings live in `~/.config/ResVibranceSwitch`. If the app is killed while a game profile is active,
your desktop settings are restored the next time it starts.

## Command line

```sh
python3 resvib.py status | run | apply <profile> | modes
```

| File | Purpose |
|---|---|
| `app.py` | GTK4 interface |
| `tray.py` | Tray icon (separate process: the tray needs GTK3) |
| `resvib.py` | Engine: display backends, resolution, vibrance, game watcher, config |
| `hotkeys.py` | Global hotkeys (Hyprland), single-instance messaging, instant focus detection |
| `detect.py` | Steam game detection and launching |
| `presets.py` | Built-in game list – add games here |
| `version.py` | Version number |

## License

[MIT](LICENSE)
