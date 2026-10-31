#!/usr/bin/env bash
# Builds the Linux program into build/linux/warpdown (run from anywhere; needs Python, requirements and pyinstaller).
#   packaging/linux/build.sh              program + connector + yt-dlp
#   packaging/linux/build.sh --helpers    also ffmpeg (for the Flatpak, which cannot use the system's)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
OUT="$ROOT/build/linux"
rm -rf "$OUT" "$ROOT/build/pyi"
mkdir -p "$OUT" "$ROOT/build/tools"

"$PY" -m PyInstaller --noconfirm --name warpdown \
  --distpath "$OUT" --workpath "$ROOT/build/pyi" --specpath "$ROOT/build/pyi" --paths "$ROOT" \
  --exclude-module yt_dlp --add-data "$ROOT/warpdown/assets:warpdown/assets" --add-data "$ROOT/extension:extension" \
  packaging/windows/run_warpdown.py
"$PY" -m PyInstaller --noconfirm --onefile --console --name warpdown-host \
  --distpath "$OUT/warpdown" --workpath "$ROOT/build/pyi" --specpath "$ROOT/build/pyi" --paths "$ROOT" \
  native/warpdown_host.py
[ -x "$OUT/warpdown/warpdown" ] && [ -x "$OUT/warpdown/warpdown-host" ] || { echo "PyInstaller failed"; exit 1; }

# yt-dlp (video sites): the official stand-alone build
curl -fsSL -o "$OUT/warpdown/yt-dlp" https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux
chmod +x "$OUT/warpdown/yt-dlp"

if [ "${1:-}" = "--helpers" ]; then
  curl -fsSL -o "$ROOT/build/tools/ffmpeg.tar.xz" \
    https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl.tar.xz
  tar -xJf "$ROOT/build/tools/ffmpeg.tar.xz" -C "$ROOT/build/tools"
  cp "$(find "$ROOT/build/tools" -type f -name ffmpeg -path '*bin*' | head -1)" "$OUT/warpdown/ffmpeg"
  chmod +x "$OUT/warpdown/ffmpeg"
fi
cp "$ROOT/LICENSE" "$OUT/warpdown/LICENSE"
echo "Built $(du -sh "$OUT/warpdown" | cut -f1) in $OUT/warpdown"
