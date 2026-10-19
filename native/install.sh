#!/usr/bin/env bash
# Registers the native messaging host for Firefox and Chromium-based browsers,
# and installs a desktop launcher. Run from the project root after creating .venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
NAME="org.kudm.host"
WRAPPER="$ROOT/native/kudm-host"
cat > "$WRAPPER" <<WRAP
#!/usr/bin/env bash
cd "$ROOT"
export PYTHONPATH="$ROOT"
exec "$PY" "$ROOT/native/kudm_host.py"
WRAP
chmod +x "$WRAPPER"

# Firefox uses allowed_extensions, Chromium-family uses allowed_origins.
FF_ID="kudm@kudm.local"
CHROME_ID="${KUDM_CHROME_EXTENSION_ID:-}"   # set after loading the unpacked extension

write_manifest() { # dir type
  mkdir -p "$1"
  if [ "$2" = firefox ]; then
    cat > "$1/$NAME.json" <<JSON
{"name":"$NAME","description":"KUDM native host","path":"$WRAPPER","type":"stdio","allowed_extensions":["$FF_ID"]}
JSON
  elif [ -n "$CHROME_ID" ]; then
    cat > "$1/$NAME.json" <<JSON
{"name":"$NAME","description":"KUDM native host","path":"$WRAPPER","type":"stdio","allowed_origins":["chrome-extension://$CHROME_ID/"]}
JSON
  fi
}
write_manifest "$HOME/.mozilla/native-messaging-hosts" firefox
for d in google-chrome chromium BraveSoftware/Brave-Browser vivaldi microsoft-edge; do
  write_manifest "$HOME/.config/$d/NativeMessagingHosts" chrome
done

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/kudm.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=KUDM Download Manager
Comment=IDM-style download manager
Exec=env PYTHONPATH=$ROOT $PY -m kudm %u
Icon=folder-download
Categories=Network;FileTransfer;
MimeType=x-scheme-handler/magnet;
Terminal=false
DESK
echo "Installed. Firefox host registered; Chromium-family hosts need KUDM_CHROME_EXTENSION_ID (see README)."
