#!/usr/bin/env bash
# Packs build/linux/warpdown (built with --helpers) into dist/warpdown-<version>.flatpak  (uses only `flatpak`).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
APPID=io.github.marius101.Warpdown
RUNTIME="${FLATPAK_RUNTIME:-org.kde.Platform}"
BRANCH="${FLATPAK_BRANCH:-6.10}"
VERSION="$(python3 -c "import re,pathlib; print(re.search(r'__version__ = \"(.+?)\"', pathlib.Path('warpdown/__init__.py').read_text()).group(1))")"
SRC="$ROOT/build/linux/warpdown"
APP="$ROOT/build/flatpak/app"
[ -x "$SRC/warpdown" ] && [ -x "$SRC/ffmpeg" ] || { echo "Run packaging/linux/build.sh --helpers first"; exit 1; }
rm -rf "$ROOT/build/flatpak" && mkdir -p "$ROOT/dist"
flatpak build-init "$APP" "$APPID" "${FLATPAK_SDK:-$RUNTIME}" "$RUNTIME" "$BRANCH"
F="$APP/files"
mkdir -p "$F/bin" "$F/share/applications" "$F/share/icons/hicolor/scalable/apps" "$F/share/metainfo"
cp -a "$SRC" "$F/warpdown"
printf '#!/bin/sh\nexec /app/warpdown/warpdown "$@"\n' > "$F/bin/warpdown" && chmod 755 "$F/bin/warpdown"
sed -e 's|@EXEC@|warpdown|' -e "s|@ICON@|$APPID|" packaging/linux/warpdown.desktop > "$F/share/applications/$APPID.desktop"
cp warpdown/assets/warpdown.svg "$F/share/icons/hicolor/scalable/apps/$APPID.svg"
sed -e "s|@VERSION@|$VERSION|" -e "s|@DATE@|$(date +%F)|" packaging/flatpak/$APPID.metainfo.xml > "$F/share/metainfo/$APPID.metainfo.xml"
chmod -R go-w "$F"

flatpak build-finish "$APP" --command=warpdown \
  --share=network --share=ipc --socket=wayland --socket=fallback-x11 --device=dri \
  --filesystem=xdg-download --filesystem=xdg-videos --filesystem=xdg-music --filesystem=xdg-documents --filesystem=xdg-pictures \
  --filesystem=~/.mozilla/native-messaging-hosts:create \
  --talk-name=org.kde.StatusNotifierWatcher --talk-name=org.freedesktop.Notifications --talk-name=org.kde.klipper \
  --system-talk-name=org.freedesktop.login1
flatpak build-export "$ROOT/build/flatpak/repo" "$APP" >/dev/null
flatpak build-bundle "$ROOT/build/flatpak/repo" "$ROOT/dist/warpdown-${VERSION}.flatpak" "$APPID" --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo
ls -la "$ROOT/dist/warpdown-${VERSION}.flatpak"
