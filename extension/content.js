// Floating "Download this video" button, like IDM's. Shown when the mouse is over a <video>.
(() => {
  const api = typeof browser !== "undefined" ? browser : chrome;
  if (window.__warpdownContent) return;
  window.__warpdownContent = true;

  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;z-index:2147483647;display:none;top:0;left:0;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      .btn{font:600 12px system-ui,sans-serif;color:#fff;background:#1d7fe0;border:0;border-radius:6px;
           padding:6px 10px;cursor:pointer;box-shadow:0 2px 8px #0008;white-space:nowrap}
      .btn:hover{background:#3b97f5}
      .btn.compact{padding:6px 7px;line-height:1}
      .btn svg{display:inline-block}
      .menu{margin-top:4px;background:#222;color:#eee;border-radius:6px;font:12px system-ui,sans-serif;
            box-shadow:0 2px 10px #000a;max-width:340px;overflow:hidden}
      .menu div{padding:6px 10px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .menu div:hover{background:#1d7fe0}
      .msg{padding:6px 10px}
    </style>
    <button class="btn"></button><div class="menu" hidden></div>`;
  // Warpdown mark: tunnel rings + down arrow (same artwork as the app icon, drawn without its tile)
  const MARK = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" style="vertical-align:-3px">
    <circle cx="12" cy="12" r="10.5" fill="none" stroke="#fff" stroke-width="1.6" opacity=".35"/>
    <circle cx="12" cy="12" r="7.5" fill="none" stroke="#fff" stroke-width="1.8" opacity=".65"/>
    <path d="M10.4 7 h3.2 v5 h2.4 L12 17 L8 12 h2.4 z" fill="#fff"/></svg>`;
  const btn = root.querySelector(".btn");
  const menu = root.querySelector(".menu");
  let current = null, hideTimer = null, overHost = false, style = "full";

  // Button style (set in the toolbar popup): "full" = arrow + text, "compact" = arrow only, "hidden" = no button.
  function applyStyle(s) {
    style = ["full", "compact", "hidden"].includes(s) ? s : "full";
    btn.className = "btn" + (style === "compact" ? " compact" : "");
    btn.innerHTML = style === "compact" ? MARK : MARK + " Download this video";
    btn.title = "Download this video";
    if (style === "hidden") hide(); else if (host.style.display === "block") place();
  }
  try {
    api.storage.local.get("buttonStyle").then(({ buttonStyle }) => applyStyle(buttonStyle));
    api.storage.onChanged.addListener(ch => { if (ch.buttonStyle) applyStyle(ch.buttonStyle.newValue); });
  } catch (e) { /* storage unavailable: keep the full button */ }

  const sizeLabel = n => (n ? (n / 1048576).toFixed(1) + " MB · " : "");
  const attach = () => { if (!host.isConnected) (document.fullscreenElement || document.documentElement).appendChild(host); };

  function videoUnderPointer(x, y) {
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      if (r.width >= 200 && r.height >= 120 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return { v, r };
    }
    return null;
  }

  // Top-right corner of the video; uses the real width so a wide menu under a compact button stays on screen.
  function place() {
    if (!current) return;
    const r = current.getBoundingClientRect();
    const w = host.offsetWidth || 190;
    host.style.left = Math.max(0, Math.min(r.right - w - 8, innerWidth - w - 4)) + "px";
    host.style.top = Math.max(0, r.top + 8) + "px";
  }

  function show(v) {
    if (style === "hidden") return;
    current = v;
    attach();
    host.style.display = "block";
    place();
    clearTimeout(hideTimer);
    if (!overHost) hideTimer = setTimeout(hide, 2500);
  }
  host.addEventListener("mouseenter", () => { overHost = true; clearTimeout(hideTimer); });
  host.addEventListener("mouseleave", () => { overHost = false; if (menu.hidden) hideTimer = setTimeout(hide, 1500); });
  function hide() { host.style.display = "none"; menu.hidden = true; }

  document.addEventListener("mousemove", e => {
    if (e.composedPath().includes(host)) { clearTimeout(hideTimer); return; }
    if (style === "hidden") return;
    const hit = videoUnderPointer(e.clientX, e.clientY);
    if (hit) show(hit.v); else if (!menu.hidden) { /* keep open while picking */ } else hide();
  }, true);

  function candidates() {
    const out = [];
    const v = current;
    if (v) {
      const srcs = [v.currentSrc, v.src, ...[...v.querySelectorAll("source")].map(s => s.src)];
      for (const s of srcs) if (s && /^https?:/.test(s) && !out.includes(s)) out.push(s);
    }
    return out;
  }

  function tracks() {
    if (!current) return [];
    return [...current.querySelectorAll("track")]
      .filter(t => t.src && /^https?:/.test(t.src) && (!t.kind || /subtitles|captions/.test(t.kind)))
      .map(t => ({ url: t.src, lang: t.srclang || "", label: t.label || "" }));
  }

  function say(text) { menu.hidden = false; menu.innerHTML = ""; const d = document.createElement("div"); d.className = "msg"; d.textContent = text; menu.append(d); place(); }

  // ---- "Download all links": list what the page offers (asked for by the background script) ----
  function collectLinks(scope) {
    const out = [], seen = new Set();
    const sel = window.getSelection();
    const hasSel = !!(sel && sel.rangeCount && !sel.isCollapsed);
    const useSel = scope === "selection" || (scope === "auto" && hasSel);
    const inScope = el => !useSel || (hasSel && sel.containsNode(el, true));
    const add = (raw, text, source) => {
      if (!raw) return;
      let u;
      try { u = new URL(raw, location.href); } catch (e) { return; }
      if (u.protocol !== "http:" && u.protocol !== "https:") return;      // no javascript:, mailto:, blob:, data: ...
      u.hash = "";
      const url = u.href;
      if (url === location.href.split("#")[0] || seen.has(url)) return;   // this very page, or already listed
      seen.add(url);
      out.push({ url, text: (text || "").replace(/\s+/g, " ").trim().slice(0, 120), source });
    };
    for (const a of document.querySelectorAll("a[href]")) if (inScope(a)) add(a.href, a.textContent || a.title || a.getAttribute("download"), "link");
    for (const i of document.images) if (inScope(i)) add(i.currentSrc || i.src, i.alt, "image");
    for (const m of document.querySelectorAll("video[src], audio[src], source[src]")) if (inScope(m)) add(m.src, "", "media");
    return { links: out.slice(0, 5000), title: document.title, page: location.href, usedSelection: useSel };
  }
  try {
    api.runtime.onMessage.addListener((msg, _sender, reply) => {
      if (msg && msg.type === "collectLinks") reply(collectLinks(msg.scope));
    });
  } catch (e) { /* messaging unavailable: the feature is simply off on this page */ }

  btn.addEventListener("click", async e => {
    e.stopPropagation();
    say("Looking for the video…");
    let r;
    try { r = await api.runtime.sendMessage({ type: "grab", candidates: candidates(), tracks: tracks(), title: document.title, page: location.href }); }
    catch (err) { return say("Extension error: " + err); }
    if (!r || r.error) return say(r && r.error || "Nothing found.");
    if (r.sent) { say(r.via === "yt-dlp" ? "Sent page to Warpdown (yt-dlp) ✓" : "Sent to Warpdown ✓"); return setTimeout(hide, 1500); }
    menu.innerHTML = "";
    for (const m of r.list) {
      const d = document.createElement("div");
      d.textContent = (m.type === "hls" ? "HLS stream · " : sizeLabel(m.size)) + (m.url.split("/").pop().split("?")[0] || m.type);
      d.title = m.url;
      d.onclick = () => { api.runtime.sendMessage({ type: "download", url: m.url, referrer: location.href, filename: m.name, subs: r.subs || [] }); hide(); };
      menu.append(d);
    }
    place();
  });
})();
