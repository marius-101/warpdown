# Warpdown status

Read this first when starting a new session. Update it at the end of each piece of work.

## Current release
**0.9.0** (25 Oct 2026) is the last published release; 0.9.1 is being released.

## Done
- Download engine, HLS, subtitles, yt-dlp support, checksum verification, signed Firefox add-on (0.8.0).
- Windows installer and portable zip, native-messaging registration, Windows 11 theme (0.8.x).
- Daily update check with a status-bar notice, "Check now" and an off switch (0.8.6).
- Linux `.deb` and Flatpak, Linux CI with smoke tests (0.9.0).
- 0.9.1 (version bumped; tag v0.9.1 builds the release):
  - A version tag builds all packages, reuses the signed `.xpi` and drafts the release (#1).
  - The update notice links the file that fits the install (#3).
  - Release notes are drafted from the commits since the previous tag (#2).
  - Chrome-family browsers: fixed extension ID, automatic connector, release zip (#4).
- Unreleased (on `main`, after 0.9.1):
  - Torrent and magnet support removed on purpose (the user prefers qBittorrent): no `torrent.py`, no aria2 in any package,
    no magnet/`.torrent` registrations. Do not add it back. Unfinished torrent entries from older versions are dropped on load.
  - *When a download completes* setting (notification, click shows the folder, optional open file / folder).

## Next
- Try the `.deb` and Flatpak on a real install (not yet tested by the user).
- Not started: DASH (`.mpd`) streams, site logins, per-site rules, Chrome Web Store / Brave listing.
- Already exist (do not rebuild): scheduler (window or trigger mode, "when finished" quit/sleep/shut down), batch / URL-pattern
  downloads, checksum verification, clipboard watcher, speed-limit schedule, VPN guard.
- Add to `install.sh`/the app: offer the Flatpak override command for Chrome-family Flatpaks from inside Warpdown.

## Working rules
- The name stays "Warpdown".
- Ask the user before every release or push; they approve explicitly.
- Release: bump `warpdown/__init__.py`, push, check the Windows and Linux CI runs, then publish with the `.xpi` attached (the README add-on link depends on it).
- Worker threads must never touch Qt objects (an AST test enforces it).
- The user tests Windows 11 in a VMware VM and reports with screenshots.
