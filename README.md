# KUDM — KUbuntu Download Manager

An open-source, IDM-style download manager for Linux (built and tested on Kubuntu).

## Features (v0.1)
- Multi-connection segmented downloads (up to 32) with **dynamic segment splitting** — an idle connection
  takes over half of the slowest remaining segment, as IDM does
- Pause / resume, also across restarts (servers that support HTTP Range)
- Queue with a configurable number of simultaneous downloads, global speed limit
- IDM-style category folders (Video, Music, Compressed, Documents, Programs, Other) created automatically and
  files sorted into them; tray icon, completion notifications
- Browser integration (Firefox + Chromium family): captures downloads, "Download with KUDM" context menu,
  a floating **Download this video** button over page videos, and a popup listing detected video/audio streams
  (direct file streams only; HLS/DASH/blob streams are on the roadmap)
- Pure Python + Qt (PySide6); no aria2 or other system packages required

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m kudm [URL]
```

## Browser integration
```bash
native/install.sh          # registers the native host (Firefox) + desktop launcher
```
- **Firefox:** `about:debugging` → This Firefox → Load Temporary Add-on → `extension/manifest.json`.
- **Chrome/Chromium/Brave:** load `extension/` unpacked, copy its extension ID, then run
  `KUDM_CHROME_EXTENSION_ID=<id> native/install.sh`.

## Tests
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

## Roadmap
HLS/DASH (m3u8) video grabbing, scheduler, site logins, per-site rules, checksum verification,
batch/URL-pattern downloads, auto-extract, packaging (.deb / Flatpak).

License: MIT
