#!/usr/bin/env bash
# Registers the native messaging host for Firefox and Chromium-based browsers,
# and installs a desktop launcher. Run from the project root after creating .venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
NAME="org.warpdown.host"
WRAPPER="$ROOT/native/warpdown-host"
cat > "$WRAPPER" <<WRAP
#!/usr/bin/env bash
cd "$ROOT"
export PYTHONPATH="$ROOT"
exec "$PY" "$ROOT/native/warpdown_host.py"
WRAP
chmod +x "$WRAPPER"

# Remove registrations from before the rename (KUDM -> Warpdown); harmless if absent.
rm -f "$HOME/.mozilla/native-messaging-hosts/org.kudm.host.json" \
      "$HOME/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts/org.kudm.host.json" \
      "$HOME/.var/app/org.mozilla.firefox/.mozilla/kudm-host-flatpak" \
      "$ROOT/native/kudm-host" "$HOME/.local/share/applications/kudm.desktop"
for d in google-chrome chromium BraveSoftware/Brave-Browser vivaldi microsoft-edge; do
  rm -f "$HOME/.config/$d/NativeMessagingHosts/org.kudm.host.json"
done

# Firefox uses allowed_extensions, Chromium-family uses allowed_origins.
FF_ID="warpdown@marius-101.github.io"            # the ID the published add-on uses
FF_ID_DEV="warpdown@warpdown.local"                # earlier temporary development builds
CHROME_ID="ildepgmogicpnhgjimbjbceknoipnhjn"    # fixed by the "key" in extension/manifest.json
CHROME_ORIGINS="\"chrome-extension://$CHROME_ID/\""
EXTRA_ID="${WARPDOWN_CHROME_EXTENSION_ID:-}"     # optional: a copy of the extension with a different ID
if [ -n "$EXTRA_ID" ] && [ "$EXTRA_ID" != "$CHROME_ID" ]; then
  CHROME_ORIGINS="$CHROME_ORIGINS,\"chrome-extension://$EXTRA_ID/\""
fi

write_manifest() { # dir type
  mkdir -p "$1"
  if [ "$2" = firefox ]; then
    cat > "$1/$NAME.json" <<JSON
{"name":"$NAME","description":"Warpdown native host","path":"$WRAPPER","type":"stdio","allowed_extensions":["$FF_ID","$FF_ID_DEV"]}
JSON
  else
    cat > "$1/$NAME.json" <<JSON
{"name":"$NAME","description":"Warpdown native host","path":"$WRAPPER","type":"stdio","allowed_origins":[$CHROME_ORIGINS]}
JSON
  fi
}
write_manifest "$HOME/.mozilla/native-messaging-hosts" firefox
for d in google-chrome chromium BraveSoftware/Brave-Browser vivaldi microsoft-edge; do
  if [ -d "$HOME/.config/$d" ]; then write_manifest "$HOME/.config/$d/NativeMessagingHosts" chrome; fi
done

# Flatpak Firefox: the sandbox cannot run host binaries directly, so go through flatpak-spawn.
FP="$HOME/.var/app/org.mozilla.firefox"
if [ -d "$FP" ]; then
  mkdir -p "$FP/.mozilla/native-messaging-hosts"
  cat > "$FP/.mozilla/warpdown-host-flatpak" <<FPW
#!/bin/sh
exec flatpak-spawn --host "$WRAPPER"
FPW
  chmod +x "$FP/.mozilla/warpdown-host-flatpak"
  cat > "$FP/.mozilla/native-messaging-hosts/$NAME.json" <<JSON
{"name":"$NAME","description":"Warpdown native host","path":"$FP/.mozilla/warpdown-host-flatpak","type":"stdio","allowed_extensions":["$FF_ID","$FF_ID_DEV"]}
JSON
  echo "Flatpak Firefox detected. Also run once:  flatpak override --user --talk-name=org.freedesktop.Flatpak org.mozilla.firefox"
fi

# Flatpak Chrome-family browsers: same flatpak-spawn trick, registered in the browser's own config folder.
for entry in com.google.Chrome:google-chrome org.chromium.Chromium:chromium com.brave.Browser:BraveSoftware/Brave-Browser; do
  APP="${entry%%:*}"; CFG="${entry#*:}"
  FP="$HOME/.var/app/$APP"
  [ -d "$FP" ] || continue
  mkdir -p "$FP/config/$CFG/NativeMessagingHosts"
  cat > "$FP/config/warpdown-host-flatpak" <<FPW
#!/bin/sh
exec flatpak-spawn --host "$WRAPPER"
FPW
  chmod +x "$FP/config/warpdown-host-flatpak"
  cat > "$FP/config/$CFG/NativeMessagingHosts/$NAME.json" <<JSON
{"name":"$NAME","description":"Warpdown native host","path":"$FP/config/warpdown-host-flatpak","type":"stdio","allowed_origins":[$CHROME_ORIGINS]}
JSON
  echo "Flatpak $APP detected. Also run once:  flatpak override --user --talk-name=org.freedesktop.Flatpak $APP"
done

mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$ROOT/warpdown/assets/warpdown.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/warpdown.svg"
cat > "$HOME/.local/share/applications/warpdown.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Warpdown Download Manager
Comment=IDM-style download manager
Exec=env PYTHONPATH=$ROOT $PY -m warpdown %u
Icon=warpdown
Categories=Network;FileTransfer;
MimeType=x-scheme-handler/magnet;application/x-bittorrent;
Terminal=false
DESK
echo "Installed. Host registered for Firefox and the Chrome-family browsers found (start a browser once, then rerun if it was missing)."
