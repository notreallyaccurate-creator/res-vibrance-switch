# Product

<!-- impeccable:product-schema 1 -->

## Platform

linux-desktop
<!-- Not one of web/ios/android/adaptive: a native Linux desktop app (Wayland-first, X11 supported). -->

## Stack

Python engine (`resvib.py`, `hotkeys.py`, `detect.py`) shelling out to desktop tools: hyprctl, kscreen-doctor, Mutter D-Bus, wlr-randr, xrandr, nvibrant, nvidia-settings.
UI: GTK4 via PyGObject (native Wayland), styled to a macOS Sequoia–like look defined in CLAUDE.md. The tray runs in a separate GTK3 process (`tray.py`).

## Users

Linux PC gamers, mainly competitive FPS players, who play at a different resolution and/or boosted digital vibrance than they use on the desktop. They run a range of distros (Arch/CachyOS, Debian/Ubuntu, Fedora, openSUSE) and desktops (Hyprland, KDE Plasma, GNOME, wlroots compositors, X11 desktops). Their job: have the right display settings the moment a game starts, and their normal desktop back when it closes, without touching anything.

## Product Purpose

Automatically switch resolution, refresh rate, digital vibrance (and brightness/contrast/gamma where the desktop allows) per game, and restore the desktop profile when the game closes or loses focus. Success: the user sets up a game once and never thinks about it again; their desktop is never left in a game's settings, even after a crash.

## Positioning

The Linux edition of Res & Vibrance Switch: same repo, same releases and same version number as the Windows app (`windows/`), but its own native UI and Linux-only feature set. It works across Linux desktops by adapting to each compositor's own mechanism (including real NVIDIA Digital Vibrance on Wayland via nvibrant, and a saturation shader on Hyprland for any GPU), rather than assuming one desktop.

## Operating Context

- Runs in the background from login, lives in the system tray (StatusNotifierItem/AppIndicator), and is opened occasionally to add or tweak games.
- Games are native Linux binaries (e.g. `cs2`) or Windows games through Proton/Wine (matched by `.exe` name); Steam libraries are detected automatically.
- Global hotkeys are registered directly on Hyprland; elsewhere the user binds `--action` commands in their desktop's shortcut settings.
- Installed per-user via `install.sh`; settings in `~/.config/ResVibranceSwitch`.

## Capabilities and Constraints

- Feature set (inherited from the Windows app): automatic per-game switching, alt-tab awareness, built-in popular FPS presets with installed-game detection, Play button, multi-monitor desktop profiles, per-game colour adjustments, global hotkeys, 15-second "keep changes?" safety revert, crash recovery, share codes. First run lands on an empty Games page that offers the installed Steam games. Removed for Linux: the Windows setup wizard and update checks (there are no Linux releases to check).
- Capability varies by desktop and must be communicated honestly: colour adjustments only on Hyprland; vibrance needs Hyprland, nvibrant (NVIDIA) or nvidia-settings (X11); focus detection unavailable on KDE/GNOME Wayland; app-registered hotkeys only on Hyprland.
- Resolutions are limited to modes the monitor reports; stretched non-native modes are pointed to gamescope.
- Presets only include games whose anti-cheat allows Linux (areweanticheatyet.com).
- Undecided: whether the product keeps the name "Res & Vibrance Switch".

## Brand Commitments

None binding yet beyond the current name (under review now that it is a separate product).

## Evidence on Hand

- Windows app README and screenshots in `docs/` (Windows product, not Linux).
- No Linux users, testimonials, benchmarks or download numbers yet — do not fabricate any.

## Product Principles

1. Never leave the user's desktop in a game's settings: restore on exit, on crash, and on logout.
2. Be truthful about what each desktop allows; hide what can't work and say why in one line.
3. Feel like a native desktop app (GTK4, macOS Sequoia–style per CLAUDE.md), not a ported Windows app.
4. Zero-touch after setup: detection and switching happen without the user opening the app.
5. Work across distros and desktops, not just one setup.
