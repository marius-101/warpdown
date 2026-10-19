const api = typeof browser !== "undefined" ? browser : chrome;
const en = document.getElementById("en");
api.storage.local.get("enabled").then(({ enabled = true }) => (en.checked = enabled));
en.onchange = () => api.storage.local.set({ enabled: en.checked });
api.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
  api.runtime.sendMessage({ type: "getMedia", tabId: tab.id }).then(list => {
    if (!list || !list.length) return;
    const box = document.getElementById("list");
    box.textContent = "";
    for (const m of list) {
      const row = document.createElement("div"); row.className = "row";
      const u = document.createElement("span"); u.className = "u"; u.title = m.url;
      u.textContent = `${m.type === "hls" ? "HLS stream " : m.size ? (m.size / 1048576).toFixed(1) + " MB " : ""}${m.url.split("/").pop().split("?")[0] || m.type}`;
      const b = document.createElement("button"); b.textContent = "Download";
      b.onclick = () => { api.runtime.sendMessage({ type: "download", url: m.url, referrer: tab.url, tabId: tab.id }); window.close(); };
      row.append(u, b); box.append(row);
    }
  });
});

api.storage.local.get("lastEvent").then(({ lastEvent }) => (document.getElementById("last").textContent = lastEvent ? "Last event: " + lastEvent : "No events yet (background not run?)"));
document.getElementById("t").onclick = () => {
  const out = document.getElementById("tr"); out.textContent = "…";
  api.runtime.sendMessage({ type: "ping" }).then(r => (out.textContent = r.ok ? "OK: host reachable" : "FAILED: " + r.error));
};

api.permissions.contains({ origins: ["<all_urls>"] }).then(ok => {
  if (ok) return;
  document.getElementById("perm").hidden = false;
  document.getElementById("grant").onclick = () =>
    api.permissions.request({ origins: ["<all_urls>"] }).then(g => { if (g) location.reload(); });
}).catch(() => {});

document.getElementById("yt").onclick = async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  const out = document.getElementById("ytr"); out.textContent = "…";
  api.runtime.sendMessage({ type: "ytdlp", url: tab.url }).then(
    r => { out.textContent = r && r.ok ? "Sent to KUDM ✓" : "Failed: " + ((r && r.error) || "KUDM not reachable"); },
    e => { out.textContent = "Failed: " + e; });
};
