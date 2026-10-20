# Privacy

Warpdown and its browser extension **collect no personal data**. There is no account, no analytics, no telemetry and no
advertising, and nothing is ever sent to the developer or to any third party.

## What the browser extension reads, and where it goes
| What | Why | Where it goes |
|---|---|---|
| The address of a download or video you start | so Warpdown can download it | only to the Warpdown app **on your own computer** (browser "native messaging"), never over the internet to us |
| The page address and title | to name the file and let yt-dlp find the video | the same local app |
| Video / audio / subtitle request addresses seen on a page | to power the **Download this video** button | kept in memory while the tab is open, sent only to the local app when you press the button |
| Cookies of the site you download from | so downloads that need a login still work | the local app, which sends them only to that same site, exactly as your browser would. **Never** forwarded for YouTube. |
| Your choices (e.g. the button style) | settings | stored in the browser's own extension storage on your computer |

The Warpdown app downloads files directly from the websites you choose, as your browser would. Its optional
video-site mode uses [yt-dlp](https://github.com/yt-dlp/yt-dlp), which also talks only to the video site itself.

## Questions
Open an issue at <https://github.com/marius-101/warpdown/issues>.
