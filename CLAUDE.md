# Res & Vibrance Switch (Linux)

Product context lives in `PRODUCT.md`. These rules govern every UI change.

## Design rules: macOS-native feel (Sequoia era, not Liquid Glass)

- **Layout:** translucent sidebar on the left (backdrop blur), a slim toolbar on top, content area on the right.
- **Typography:** system font stack (`-apple-system, system-ui, Inter` as fallback), 13–14px base size, tight and compact.
- **Color:** neutral grays, one accent color `#007AFF` used sparingly, full light and dark mode.
- **Surfaces:** 1px hairline borders, soft subtle shadows, 8–12px corner radius.
- **Controls:** compact buttons, segmented controls, toggles, grouped list rows like System Settings.
- **Motion:** short, subtle transitions, nothing bouncy.
- **Do NOT** draw fake window chrome or traffic-light buttons.

### How these map to GTK4 (the UI toolkit)

- All styling lives in one CSS template in `app.py`, rendered per theme (light/dark); never hardcode colours in widgets.
- Window controls come from the real GTK header bar and the user's decoration layout, never drawn by us.
- Backdrop blur is the compositor's job: the sidebar is translucent only where the compositor blurs it (Hyprland);
  elsewhere it falls back to an opaque sidebar tint so the wallpaper never shows through sharp.
- The accent is for: selected sidebar row, switch "on", slider fill, the single default button per view, focus rings.
  Everything else is neutral gray.
- Transitions: 120–200 ms, ease-out, opacity/colour only. No springs, no scaling bounces.

## Product rules

- Only ship features that work on Linux. When the current desktop can't support a control
  (colour adjustments, alt-tab detection, app-registered hotkeys, vibrance), hide it and say why in one line.
- Presets only list games whose anti-cheat allows Linux (checked against areweanticheatyet.com).
- The desktop must always be restored: on game exit, app quit, logout (SIGTERM) and after a crash.

## Layout of the code

- `app.py` – GTK4 UI
- `tray.py` – tray icon, run as a separate process (GTK3 AppIndicator can't share a process with GTK4)
- `resvib.py` – engine: display backends, vibrance, watcher, config
- `hotkeys.py` – IPC socket, Hyprland binds, focus events
- `detect.py` – Steam detection and launching; `presets.py` – built-in games
