#!/bin/sh
# Builds dist/res-vibrance-switch_<version>_all.deb for Debian 13 / Ubuntu 24.04 and newer (GTK 4.12+).
# Needs: python3 with Pillow, curl, ar (binutils), tar with xz. No dpkg required.
set -e
cd "$(dirname "$0")"

PKG=res-vibrance-switch
VERSION=$(python3 -c "from version import __version__; print(__version__)")
PYSTRAY=0.19.5
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
DATA="$ROOT/data"
LIB="$DATA/usr/lib/$PKG"
DOC="$DATA/usr/share/doc/$PKG"
mkdir -p "$LIB" "$DOC" "$DATA/usr/bin" "$DATA/usr/share/applications" \
    "$DATA/usr/share/icons/hicolor/256x256/apps" "$ROOT/control" dist

cp app.py detect.py hotkeys.py presets.py resvib.py tray.py version.py "$LIB/"
cp ../LICENSE "$DOC/copyright"

# Debian and Ubuntu don't package pystray, so it ships next to the app (LGPL-3.0; licence kept in the doc dir).
python3 - "$ROOT" "$PYSTRAY" <<'EOF'
import hashlib, json, sys, urllib.request, zipfile, io
root, version = sys.argv[1], sys.argv[2]
meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/pystray/{version}/json"))
wheel = next(f for f in meta["urls"] if f["packagetype"] == "bdist_wheel")
data = urllib.request.urlopen(wheel["url"]).read()
assert hashlib.sha256(data).hexdigest() == wheel["digests"]["sha256"], "pystray wheel checksum mismatch"
z = zipfile.ZipFile(io.BytesIO(data))
for name in z.namelist():
    if name.startswith("pystray/"):
        z.extract(name, f"{root}/data/usr/lib/res-vibrance-switch")
    elif name.endswith(("COPYING", "COPYING.LGPL")):
        open(f"{root}/data/usr/share/doc/res-vibrance-switch/pystray-{name.rsplit('/', 1)[1]}", "wb").write(z.read(name))
EOF

python3 -c "import tray; tray.make_icon_image(256).save('$DATA/usr/share/icons/hicolor/256x256/apps/$PKG.png')"

cat > "$DATA/usr/bin/$PKG" <<EOF
#!/bin/sh
exec python3 /usr/lib/$PKG/app.py "\$@"
EOF
chmod 755 "$DATA/usr/bin/$PKG"

# Named after the app id so Wayland compositors match the window to its icon.
cat > "$DATA/usr/share/applications/io.github.notreallyaccurate.ResVibranceSwitch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Res & Vibrance Switch
Comment=Automatic resolution and digital vibrance for every game
Exec=$PKG
Icon=$PKG
Categories=Game;Settings;
EOF

find "$DATA" -type d -exec chmod 755 {} +
find "$DATA" -type f ! -path "*/usr/bin/*" -exec chmod 644 {} +

cat > "$ROOT/control/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: all
Maintainer: notreallyaccurate-creator <238037680+notreallyaccurate-creator@users.noreply.github.com>
Installed-Size: $(du -sk "$DATA" | cut -f1)
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0 (>= 4.12), gir1.2-ayatanaappindicator3-0.1, python3-pil, python3-six, libnotify-bin
Recommends: x11-xserver-utils
Suggests: wlr-randr
Section: utils
Priority: optional
Homepage: https://github.com/notreallyaccurate-creator/res-vibrance-switch
Description: Automatic resolution and digital vibrance for every game
 Switches resolution, refresh rate and digital vibrance when a game starts
 and restores your desktop when it closes. Works on Hyprland, KDE Plasma,
 GNOME, wlroots compositors and X11. NVIDIA vibrance on Wayland needs
 nvibrant (pipx install nvibrant).
EOF
(cd "$DATA" && find . -type f ! -path ./DEBIAN -printf '%P\0' | sort -z | xargs -0 md5sum) > "$ROOT/control/md5sums"

tar --owner=0 --group=0 --numeric-owner --sort=name -C "$ROOT/control" -cJf "$ROOT/control.tar.xz" ./control ./md5sums
tar --owner=0 --group=0 --numeric-owner --sort=name -C "$DATA" -cJf "$ROOT/data.tar.xz" .
echo "2.0" > "$ROOT/debian-binary"
OUT="dist/${PKG}_${VERSION}_all.deb"
rm -f "$OUT"
(cd "$ROOT" && ar rcD "$OLDPWD/$OUT" debian-binary control.tar.xz data.tar.xz)
echo "Built $OUT"
