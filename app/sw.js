/* Prayer Times PWA service worker — cache the self-contained app shell so the
   app opens instantly and works fully offline after first load.
   /api/* is NEVER intercepted: those requests must reach the backend (the app
   falls back to its local 239-country table when they fail). */
const CACHE_NAME = 'prayer-times-shell-v2';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    self.caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => self.caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;          // same-origin only
  if (url.pathname.startsWith('/api/')) return;            // never cache API (url.path is undefined)
  if (event.request.method !== 'GET') return;

  // Network-first: always try the live file (so ships reach visitors), and only
  // fall back to the cached shell when the network is genuinely unavailable.
  // Previous strategy was cache-first with no revalidation: the first build ever
  // cached kept being served forever (plain navigations never updated). Cache
  // name bumped to v2; the activate handler evicts the old cache.
  event.respondWith(
    self.caches.open(CACHE_NAME).then((cache) =>
      fetch(event.request).then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          cache.put(event.request, copy);
        }
        return resp;
      }).catch(() => cache.match(event.request))            // offline -> cached shell
    ).catch(() => fetch(event.request))
  );
});