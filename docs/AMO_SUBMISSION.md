# Submitting the Firefox add-on (addons.mozilla.org)

Copy-paste texts for the submission forms. The file to upload is **`dist/warpdown.xpi`** (build it with
`native/build_xpi.sh`).

## Add-on details
- **Name:** Warpdown Integration
- **Add-on URL (slug):** `warpdown`
- **Summary (max 250 characters):**
  Send downloads and web videos to the Warpdown download manager for Linux: fast multi-connection downloads, a
  "Download this video" button, subtitles and yt-dlp support. Requires the free Warpdown desktop app.
- **Categories:** Download Management
- **Tags:** download manager, video downloader, hls, yt-dlp, subtitles
- **Support site:** https://github.com/marius-101/warpdown
- **Support email:** *(use your GitHub no-reply or a public contact address of your choice)*
- **License:** MIT License
- **Privacy policy:** https://github.com/marius-101/warpdown/blob/main/PRIVACY.md  *(works once the project is pushed)*
- **Data collection (in the manifest and on the form):** none

## Description
Warpdown Integration connects Firefox to **Warpdown**, a free and open-source download manager for Linux (MIT licence).

**This add-on needs the Warpdown desktop app.** Install it first: https://github.com/marius-101/warpdown

What it does:
- Takes over your browser downloads and hands them to Warpdown for fast, resumable, multi-connection downloading.
- Adds a **Download this video** button over web videos (full, compact or hidden - your choice in the toolbar popup).
- Finds HLS video streams, subtitles, and uses yt-dlp for sites such as YouTube.
- Right-click menu entries to download a link or the video on the current page.

Privacy: nothing is collected. Addresses and cookies are passed only to the Warpdown app on your own computer.
See the privacy policy for details.

## Notes for the reviewer
- The add-on is a thin front end: it talks to the **Warpdown desktop app on the same computer** through Firefox's
  native messaging (`org.warpdown.host`). Without the app installed, the popup opens but handing over a download fails
  with a message; install steps are in the project README.
- All code is plain, un-minified JavaScript with no third-party libraries and no remote code. Source:
  https://github.com/marius-101/warpdown/tree/main/extension
- Why each permission is needed:

| Permission | Reason |
|---|---|
| `downloads` | to take over a browser download (cancel it and pass it to Warpdown) |
| `nativeMessaging` | to talk to the Warpdown desktop app |
| `cookies` | to pass the download site's login cookies to the app so authenticated downloads work (never for YouTube) |
| `webRequest` | to notice video, audio, HLS and subtitle requests of a page, for the Download this video button |
| `contextMenus` | the "Download with Warpdown" right-click entries |
| `storage` | remembers settings (e.g. button style) |
| `tabs` | page address/title of the active tab, for naming files and the toolbar popup |
| host access `<all_urls>` | the video button and detection must work on any website the user watches videos on |

## Version notes (first release)
First public release: download hand-off, video button, HLS and subtitle detection, yt-dlp mode.
