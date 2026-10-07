#!/bin/sh
# Installs Res & Vibrance Switch for the current user: ./install.sh
set -e
cd "$(dirname "$0")"

# System packages: GTK4 + PyGObject (with cairo) for the window, AppIndicator for the tray, libnotify.
# Only asks for sudo when something is actually missing.
if command -v pacman >/dev/null; then
    PKGS="python python-gobject python-cairo gtk4 libayatana-appindicator libnotify"
    pacman -T $PKGS >/dev/null || sudo pacman -S --needed --noconfirm $PKGS
elif command -v apt-get >/dev/null; then
    PKGS="python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-ayatanaappindicator3-0.1 libnotify-bin"
    dpkg -s $PKGS >/dev/null 2>&1 || sudo apt-get install -y $PKGS
elif command -v dnf >/dev/null; then
    PKGS="python3-gobject python3-cairo gtk4 libayatana-appindicator-gtk3 libnotify"
    rpm -q $PKGS >/dev/null 2>&1 || sudo dnf install -y $PKGS
elif command -v zypper >/dev/null; then
    PKGS="python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-AyatanaAppIndicator3-0_1 libnotify-tools"
    rpm -q $PKGS >/dev/null 2>&1 || sudo zypper install -y $PKGS
else
    echo "Unknown distro: install Python 3 with PyGObject, pycairo, GTK 4, Ayatana AppIndicator and libnotify."
fi

DIR="${XDG_DATA_HOME:-$HOME/.local/share}/res-vibrance-switch"
mkdir -p "$DIR" "$HOME/.local/bin" "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
rm -f "$DIR"/*.py
cp ./*.py "$DIR/"
# System site packages so the venv sees the distro's PyGObject (needed for the tray and GNOME).
python3 -m venv --system-site-packages "$DIR/venv"
"$DIR/venv/bin/pip" install -q -r requirements.txt
# Real NVIDIA Digital Vibrance on Wayland (needs nvidia_drm.modeset=1, the default on current drivers).
if [ -e /dev/nvidia-modeset ]; then
    "$DIR/venv/bin/pip" install -q nvibrant
fi

cat > "$HOME/.local/bin/res-vibrance-switch" <<EOF
#!/bin/sh
PATH="$DIR/venv/bin:\$PATH" exec "$DIR/venv/bin/python" "$DIR/app.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/res-vibrance-switch"

ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$ICONS"
"$DIR/venv/bin/python" -c "import sys; sys.path.insert(0, '$DIR'); import tray; \
tray.make_icon_image(256).save('$ICONS/res-vibrance-switch.png')"

# Named after the app id so Wayland compositors match the window to its icon.
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/io.github.notreallyaccurate.ResVibranceSwitch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Res & Vibrance Switch
Comment=Automatic resolution and digital vibrance for every game
Exec=$HOME/.local/bin/res-vibrance-switch
Icon=res-vibrance-switch
Categories=Game;Settings;
EOF

echo "Installed. Start it from your app menu or run: res-vibrance-switch"
case "$XDG_CURRENT_DESKTOP" in
    *GNOME*) echo "GNOME: install the 'AppIndicator and KStatusNotifierItem Support' extension to see the tray icon." ;;
esac
if [ -n "$WAYLAND_DISPLAY" ] && [ -z "$HYPRLAND_INSTANCE_SIGNATURE" ] && ! echo "$XDG_CURRENT_DESKTOP" | grep -qiE 'kde|gnome'; then
    echo "wlroots compositors (sway, river, niri, labwc...): also install wlr-randr."
fi
