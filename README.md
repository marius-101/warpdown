# Warpdown — fast download manager

An open-source, [Internet Download Manager](https://www.internetdownloadmanager.com/)-style download manager for
Linux, built and tested on Kubuntu (KDE Plasma, Wayland). Fast multi-connection downloads, a browser extension that
takes over downloads and grabs videos, subtitles, and YouTube support through yt-dlp.

![Warpdown main window](docs/screenshot.png)

## Features
- **Fast segmented downloads** — up to 32 connections, with *dynamic segment splitting*: a connection that finishes
  early takes over half of the largest remaining piece, so all connections stay busy to the end
- **Pause / resume**, also across restarts (servers that support HTTP Range)
- **Queue** with a configurable number of simultaneous downloads and a global speed limit
- **IDM-style category folders** — Video, Music, Compressed, Documents, Programs, Other — created automatically;
  files are sorted into them (file types are recognised from the name, `Content-Type` or the file's first bytes)
- **Browser integration** (Firefox and Chromium family): takes over browser downloads, a "Download with Warpdown"
  context menu, and a floating **Download this video** button over page videos
- **Video streams**: plain files and **HLS (`.m3u8`)** streams, including AES-128 encrypted ones, merged to MP4
  with ffmpeg. *DRM-protected streams are not supported and never will be.*
- **Subtitles**: pick which tracks to save (`.vtt` / `.srt` / `.ass`), stored next to the video with the language in
  the name (`Movie.eng.vtt`) so players like Haruna and mpv pick them up automatically
- **yt-dlp mode** for YouTube, Vimeo and many other sites: choose quality (best / 1080p / … / audio-only MP3),
  H.264/AAC MP4 output, subtitles. The browser button falls back to it when a page has no plain file or HLS stream
- Tray icon, completion notifications, pure Python + Qt (PySide6) — no aria2 or other daemons needed

## Install (Kubuntu / Ubuntu / Debian)
```bash
sudo apt install python3-venv ffmpeg libxcb-cursor0     # ffmpeg: merging video/audio; libxcb-cursor0: Qt on X11
git clone https://github.com/marius-101/warpdown.git
cd warpdown
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m warpdown                                 # start Warpdown (add a URL as an argument to download it)
```
For **full YouTube quality** also install a JavaScript runtime: `sudo apt install nodejs`.

## Browser integration
```bash
native/install.sh        # registers the native-messaging host and a desktop launcher
```
**Firefox**
1. `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on…* → pick **`dist/warpdown.xpi`**
   (build it first with `native/build_xpi.sh`). A temporary add-on has to be loaded again after Firefox restarts.
2. Click the Warpdown toolbar icon; if it shows a yellow notice, press **Grant access** (needed for the video button).
3. *Flatpak Firefox?* The installer prints one extra command to allow the native host; run it and restart Firefox.

**Chrome / Chromium / Brave** — load the `extension/` folder unpacked, copy the extension ID, then run
`WARPDOWN_CHROME_EXTENSION_ID=<id> native/install.sh`.

### Using it
- Click a download link as usual — Warpdown takes over and asks where to save it.
- Hover over a video and press **Download this video**. On sites where the page itself is the source
  (YouTube, …) it opens Warpdown's yt-dlp mode.
- Settings has the default folder, connections, speed limit, subtitle languages, and an **Update yt-dlp** button
  (video sites change often; if a site stops working, update first).

## Development
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest      # tests run against local servers; no internet needed
```
Layout: `warpdown/` (engine, HLS, subtitles, yt-dlp, manager, Qt GUI), `extension/` (browser extension),
`native/` (native-messaging host + installer), `tests/`.

## Roadmap
**Windows support** (installer + native-messaging registration), DASH (`.mpd`) streams, scheduler, site logins, per-site rules, checksum verification, batch / URL-pattern downloads,
signed Firefox add-on, `.deb` / Flatpak packaging.

## Responsible use
Warpdown is a tool; you are responsible for what you download. Respect copyright and the terms of the sites you use.

## License
[MIT](LICENSE)
