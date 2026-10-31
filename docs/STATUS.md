# Warpdown status

Read this first when starting a new session. Update it at the end of each piece of work.

## Current release
**0.9.0** (25 Oct 2026): first release with the Linux `.deb` and Flatpak, built by `linux.yml` and `windows.yml`.

## Done
- Download engine, HLS, subtitles, yt-dlp support, checksum verification, signed Firefox add-on (0.8.0).
- Windows installer and portable zip, native-messaging registration, Windows 11 theme (0.8.x).
- Daily update check with a status-bar notice, "Check now" and an off switch (0.8.6).
- Linux `.deb` and Flatpak, Linux CI with smoke tests (0.9.0).
- After 0.9.0 (merged, not yet released):
  - A version tag builds all packages, reuses the signed `.xpi` and drafts the release (#1).
  - The update notice links the file that fits the install (#3).
  - Release notes are drafted from the commits since the previous tag (#2).
  - Chrome-family browsers: fixed extension ID, automatic connector, release zip (#4).

## Next
- Try the 0.9.0 `.deb` and Flatpak on a real install (not yet tested by the user).
- Test the Chrome-family support on a real browser, then release it (0.9.1 or 0.10.0).
- Roadmap features not started: DASH (`.mpd`) streams, scheduler, site logins, per-site rules, batch / URL-pattern downloads.
- Clean up the README "Roadmap" section: it still lists shipped items (Windows, signed add-on, packaging).

## Working rules
- The name stays "Warpdown".
- Ask the user before every release or push; they approve explicitly.
- Release: bump `warpdown/__init__.py`, push, check the Windows and Linux CI runs, then publish with the `.xpi` attached (the README add-on link depends on it).
- Worker threads must never touch Qt objects (an AST test enforces it).
- The user tests Windows 11 in a VMware VM and reports with screenshots.
