const api = typeof browser !== "undefined" ? browser : chrome;
const HOST = "org.warpdown.host";
const media = {}; // tabId -> [{url, type, size}]
const subsByTab = {}; // tabId -> [{url, lang, label}] detected .vtt/.srt/.ass requests
console.log("[Warpdown] background loaded");

async function cookieHeader(url) {
  try {
    const cs = await api.cookies.getAll({ url });
    return cs.map(c => `${c.name}=${c.value}`).join("; ");
  } catch { return ""; }
}

async function sendToWarpdown(url, extra = {}) {
  const cookie = await cookieHeader(url);
  return api.runtime.sendNativeMessage(HOST, {
    action: "add", url,
    headers: { cookie, referer: extra.referrer || "", user_agent: navigator.userAgent },
    filename: extra.filename || "",
    subs: extra.subs || [],
    mode: extra.mode || "",
  });
}

const EXT = { "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
  "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/aac": ".aac" };
function nameFor(title, m) {
  if (m.type === "hls") m = { ...m, url: "x.mp4" };
  let ext = (m.url.split("?")[0].match(/\.(mp4|webm|mkv|mov|flv|mp3|m4a|ogg|aac)$/i) || [])[0] || EXT[(m.type || "").split(";")[0]] || "";
  const base = (title || "video").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").trim().slice(0, 120) || "video";
  return base + ext;
}
// Chunked streaming URLs (YouTube-style &range=…) are fragments, not whole files.
const isFragment = u => /[?&](range|bytes|sq|seg)=/i.test(u) || /\/videoplayback/.test(u) || /\.(ts|m4s|aac)(\?|$)/i.test(u);

async function note(msg) {
  console.log("[Warpdown]", msg);
  await api.storage.local.set({ lastEvent: `${new Date().toLocaleTimeString()} ${msg}` });
}
note("background started");

// Take over normal browser downloads.
api.downloads.onCreated.addListener(async (d) => {
  const url = d.finalUrl || d.url;
  await note("download created: " + url.slice(0, 80));
  if (!/^https?:/.test(url)) return;
  const { enabled = true } = await api.storage.local.get("enabled");
  if (!enabled) return;
  try {
    await api.downloads.cancel(d.id);
    await api.downloads.erase({ id: d.id });
    const r = await sendToWarpdown(url, { referrer: d.referrer });
    await note("host replied: " + JSON.stringify(r));
    if (!r || !r.ok) throw new Error("Warpdown not reachable");
  } catch (e) {
    await note("handoff FAILED: " + (e && e.message || e));
    api.downloads.download({ url });
  }
});

// Right-click "Download with Warpdown".
api.runtime.onInstalled.addListener(() => {
  api.contextMenus.create({ id: "warpdown-link", title: "Download with Warpdown", contexts: ["link", "video", "audio"] });
  api.contextMenus.create({ id: "warpdown-page", title: "Download this page's video with Warpdown (yt-dlp)", contexts: ["page"] });
});
api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "warpdown-page") return sendToWarpdown(info.pageUrl, { referrer: info.pageUrl, mode: "ytdlp" });
  const url = info.linkUrl || info.srcUrl;
  if (url) sendToWarpdown(url, { referrer: tab && tab.url });
});

// Detect media streams per tab (for the popup).
api.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0) return;
  const h = Object.fromEntries(d.responseHeaders.map(x => [x.name.toLowerCase(), x.value || ""]));
  const type = h["content-type"] || "";
  const isSub = /\.(vtt|srt|ass|ssa)(\?|$)/i.test(d.url) || /text\/vtt|x-subrip|x-ssa/i.test(type);
  if (isSub && d.statusCode < 400 && !/thumb|sprite|storyboard/i.test(d.url)) {
    const sl = (subsByTab[d.tabId] = subsByTab[d.tabId] || []);
    if (!sl.some(x => x.url === d.url)) sl.push({ url: d.url, lang: "", label: "" });
    return;
  }
  const isHls = /mpegurl/i.test(type) || /\.m3u8(\?|$)/i.test(d.url);
  if (isHls && d.statusCode < 400) {
    const l = (media[d.tabId] = media[d.tabId] || []);
    if (!l.some(m => m.url === d.url)) l.push({ url: d.url, type: "hls", size: 0 });
    return;
  }
  const isMedia = (/^(video|audio)\//.test(type) && !/mp2t/.test(type)) || /\.(mp4|webm|mkv|mp3|m4a|flv)(\?|$)/i.test(d.url);
  if (!isMedia || d.statusCode >= 400 || isFragment(d.url)) return;
  const size = parseInt((h["content-range"] || "").split("/")[1] || h["content-length"] || "0", 10);
  if (!size || size < 1024 * 1024) return; // ignore tiny/unknown-size files (UI sounds, segments, trackers)
  const list = (media[d.tabId] = media[d.tabId] || []);
  if (!list.some(m => m.url === d.url)) list.push({ url: d.url, type, size });
}, { urls: ["<all_urls>"] }, ["responseHeaders"]);
api.tabs.onRemoved.addListener(id => { delete media[id]; delete subsByTab[id]; });
api.tabs.onUpdated.addListener((id, ch) => { if (ch.status === "loading" && ch.url) { delete media[id]; delete subsByTab[id]; } });

api.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "ping") {
    api.runtime.sendNativeMessage(HOST, { action: "ping" }).then(
      r => reply({ ok: true, r }), e => reply({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === "grab") {
    const tabId = sender.tab && sender.tab.id;
    const seen = new Set(), list = [];
    const add = (url, type, size) => { if (!seen.has(url) && !isFragment(url)) { seen.add(url); list.push({ url, type: type || "", size: size || 0 }); } };
    for (const u of msg.candidates || []) add(u);
    for (const m of media[tabId] || []) add(m.url, m.type, m.size);
    if (!list.length) {
      // Nothing downloadable as a plain file/HLS: let Warpdown try yt-dlp on the page itself (YouTube etc.).
      sendToWarpdown(msg.page, { referrer: msg.page, mode: "ytdlp" }).then(
        r => reply(r && r.ok ? { sent: true, via: "yt-dlp" } : { error: "Warpdown not reachable" }),
        e => reply({ error: String(e && e.message || e) }));
      return true;
    }
    const nf = m => nameFor(msg.title, m);
    const subs = [];
    for (const t of [...(msg.tracks || []), ...(subsByTab[tabId] || [])]) if (!subs.some(x => x.url === t.url)) subs.push(t);
    if (list.length === 1) {
      sendToWarpdown(list[0].url, { referrer: msg.page, filename: nf(list[0]), subs }).then(
        r => reply(r && r.ok ? { sent: true } : { error: "Warpdown not reachable" }),
        e => reply({ error: String(e && e.message || e) }));
    } else {
      list.sort((a, b) => (b.type === "hls") - (a.type === "hls") || b.size - a.size);
      // functions can't cross the message boundary: send precomputed names instead
      reply({ list: list.map(m => ({ ...m, name: nf(m) })), subs });
    }
    return true;
  }
  if (msg.type === "ytdlp") {
    sendToWarpdown(msg.url, { referrer: msg.url, mode: "ytdlp" }).then(reply, e => reply({ ok: false, error: String(e) }));
    return true;
  }
  if (msg.type === "getMedia") reply(media[msg.tabId] || []);
  if (msg.type === "download") sendToWarpdown(msg.url, { referrer: msg.referrer, filename: msg.filename, subs: msg.subs || (subsByTab[msg.tabId] || []) }).then(reply);
  return true;
});
