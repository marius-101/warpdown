const api = typeof browser !== "undefined" ? browser : chrome;
const HOST = "org.kudm.host";
const media = {}; // tabId -> [{url, type, size}]
console.log("[KUDM] background loaded");

async function cookieHeader(url) {
  try {
    const cs = await api.cookies.getAll({ url });
    return cs.map(c => `${c.name}=${c.value}`).join("; ");
  } catch { return ""; }
}

async function sendToKudm(url, extra = {}) {
  const cookie = await cookieHeader(url);
  return api.runtime.sendNativeMessage(HOST, {
    action: "add", url,
    headers: { cookie, referer: extra.referrer || "", user_agent: navigator.userAgent },
    filename: extra.filename || "",
  });
}

async function note(msg) {
  console.log("[KUDM]", msg);
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
    const r = await sendToKudm(url, { referrer: d.referrer });
    await note("host replied: " + JSON.stringify(r));
    if (!r || !r.ok) throw new Error("KUDM not reachable");
  } catch (e) {
    await note("handoff FAILED: " + (e && e.message || e));
    api.downloads.download({ url });
  }
});

// Right-click "Download with KUDM".
api.runtime.onInstalled.addListener(() => {
  api.contextMenus.create({ id: "kudm-link", title: "Download with KUDM", contexts: ["link", "video", "audio"] });
});
api.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.linkUrl || info.srcUrl;
  if (url) sendToKudm(url, { referrer: tab && tab.url });
});

// Detect media streams per tab (for the popup).
api.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0) return;
  const h = Object.fromEntries(d.responseHeaders.map(x => [x.name.toLowerCase(), x.value || ""]));
  const type = h["content-type"] || "";
  const isMedia = /^(video|audio)\//.test(type) || /\.(mp4|webm|mkv|mp3|m4a|flv)(\?|$)/i.test(d.url);
  if (!isMedia || d.statusCode >= 400) return;
  const size = parseInt((h["content-range"] || "").split("/")[1] || h["content-length"] || "0", 10);
  if (size && size < 200 * 1024) return; // ignore tiny segments
  const list = (media[d.tabId] = media[d.tabId] || []);
  if (!list.some(m => m.url === d.url)) list.push({ url: d.url, type, size });
}, { urls: ["<all_urls>"] }, ["responseHeaders"]);
api.tabs.onRemoved.addListener(id => delete media[id]);
api.tabs.onUpdated.addListener((id, ch) => { if (ch.status === "loading" && ch.url) delete media[id]; });

api.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg.type === "ping") {
    api.runtime.sendNativeMessage(HOST, { action: "ping" }).then(
      r => reply({ ok: true, r }), e => reply({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === "getMedia") reply(media[msg.tabId] || []);
  if (msg.type === "download") sendToKudm(msg.url, { referrer: msg.referrer }).then(reply);
  return true;
});
