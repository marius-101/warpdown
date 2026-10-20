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
      .menu{margin-top:4px;background:#222;color:#eee;border-radius:6px;font:12px system-ui,sans-serif;
            box-shadow:0 2px 10px #000a;max-width:340px;overflow:hidden}
      .menu div{padding:6px 10px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .menu div:hover{background:#1d7fe0}
      .msg{padding:6px 10px}
    </style>
    <button class="btn">⬇ Download this video</button><div class="menu" hidden></div>`;
  const btn = root.querySelector(".btn");
  const menu = root.querySelector(".menu");
  let current = null, hideTimer = null, overHost = false;

  const sizeLabel = n => (n ? (n / 1048576).toFixed(1) + " MB · " : "");
  const attach = () => { if (!host.isConnected) (document.fullscreenElement || document.documentElement).appendChild(host); };

  function videoUnderPointer(x, y) {
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      if (r.width >= 200 && r.height >= 120 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return { v, r };
    }
    return null;
  }

  function show(v, r) {
    current = v;
    attach();
    host.style.left = Math.max(0, Math.min(r.right - 190, innerWidth - 200)) + "px";
    host.style.top = Math.max(0, r.top + 8) + "px";
    host.style.display = "block";
    clearTimeout(hideTimer);
    if (!overHost) hideTimer = setTimeout(hide, 2500);
  }
  host.addEventListener("mouseenter", () => { overHost = true; clearTimeout(hideTimer); });
  host.addEventListener("mouseleave", () => { overHost = false; if (menu.hidden) hideTimer = setTimeout(hide, 1500); });
  function hide() { host.style.display = "none"; menu.hidden = true; }

  document.addEventListener("mousemove", e => {
    if (e.composedPath().includes(host)) { clearTimeout(hideTimer); return; }
    const hit = videoUnderPointer(e.clientX, e.clientY);
    if (hit) show(hit.v, hit.r); else if (!menu.hidden) { /* keep open while picking */ } else hide();
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

  function say(text) { menu.hidden = false; menu.innerHTML = ""; const d = document.createElement("div"); d.className = "msg"; d.textContent = text; menu.append(d); }

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
  });
})();
