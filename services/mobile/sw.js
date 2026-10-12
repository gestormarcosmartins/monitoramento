const CACHE = "nexora-m-v2";
const SHELL = ["/m/", "/m/styles.css", "/m/app.js", "/m/icon.svg", "/m/icon-512.png", "/m/manifest.webmanifest"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((k) => Promise.all(k.filter((x) => x !== CACHE).map((x) => caches.delete(x)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  // Só a casca do app é cacheada; API e login nunca passam pelo cache.
  if (e.request.method !== "GET" || u.origin !== location.origin || !u.pathname.startsWith("/m/")) return;
  e.respondWith(fetch(e.request).then((r) => { const c = r.clone(); caches.open(CACHE).then((x) => x.put(e.request, c)); return r; }).catch(() => caches.match(e.request).then((r) => r || caches.match("/m/"))));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch {}
  e.waitUntil(self.registration.showNotification(d.title || "Nexora", { body: d.body || "", tag: d.tag, icon: "/m/icon.svg", badge: "/m/icon.svg", data: { url: d.url || "/m/" } }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/m/", location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith("/m/")) return;
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then((l) => {
    for (const c of l) if (c.url.startsWith(location.origin + "/m/") && "focus" in c) { c.postMessage({ open: url.search }); return c.focus(); }
    return clients.openWindow(url.href);
  }));
});
