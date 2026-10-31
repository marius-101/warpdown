#!/usr/bin/env bash
# Packs build/linux/warpdown into dist/warpdown_<version>_amd64.deb  (run build.sh first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(python3 -c "import re,pathlib; print(re.search(r'__version__ = \"(.+?)\"', pathlib.Path('warpdown/__init__.py').read_text()).group(1))")"
SRC="$ROOT/build/linux/warpdown"
PKG="$ROOT/build/deb/warpdown_${VERSION}_amd64"
[ -x "$SRC/warpdown" ] || { echo "Run packaging/linux/build.sh first"; exit 1; }
rm -rf "$ROOT/build/deb" && mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/icons/hicolor/256x256/apps" \
  "$PKG/usr/lib/mozilla/native-messaging-hosts" "$PKG/usr/share/doc/warpdown" "$ROOT/dist"

cp -a "$SRC" "$PKG/opt/warpdown"
ln -s ../../opt/warpdown/warpdown "$PKG/usr/bin/warpdown"
sed -e 's|@EXEC@|/usr/bin/warpdown|' -e 's|@ICON@|warpdown|' packaging/linux/warpdown.desktop > "$PKG/usr/share/applications/warpdown.desktop"
cp warpdown/assets/warpdown.svg "$PKG/usr/share/icons/hicolor/scalable/apps/warpdown.svg"
cp warpdown/assets/warpdown.png "$PKG/usr/share/icons/hicolor/256x256/apps/warpdown.png"
cat > "$PKG/usr/lib/mozilla/native-messaging-hosts/org.warpdown.host.json" <<JSON
{"name": "org.warpdown.host", "description": "Warpdown native host", "path": "/opt/warpdown/warpdown-host", "type": "stdio",
 "allowed_extensions": ["warpdown@marius-101.github.io", "warpdown@warpdown.local"]}
JSON
cp LICENSE "$PKG/usr/share/doc/warpdown/copyright"
SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTL
Package: warpdown
Version: ${VERSION}
Section: net
Priority: optional
Architecture: amd64
Installed-Size: ${SIZE}
Depends: libc6 (>= 2.35), libegl1, libgl1, libfontconfig1, libdbus-1-3, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1
Recommends: ffmpeg
Suggests: nodejs
Homepage: https://github.com/marius-101/warpdown
Maintainer: Warpdown project <noreply@github.com>
Description: fast download manager with browser integration
 Segmented, resumable downloads (like IDM), a Firefox/Chrome extension, video sites
 through yt-dlp, HLS streams, scheduler,
 speed limits, checksum verification and a clipboard watcher.
CTL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
exit 0
POST
cp "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod -R go-w "$PKG"
find "$PKG" -type d -exec chmod 755 {} +
fakeroot dpkg-deb -Zxz -z6 --build --root-owner-group "$PKG" "$ROOT/dist/warpdown_${VERSION}_amd64.deb" >/dev/null
ls -la "$ROOT/dist/warpdown_${VERSION}_amd64.deb"
