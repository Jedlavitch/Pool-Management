// Mar-A-Lavitch Staff Service Worker
// Enables PWA install prompts and "Add to Home Screen" on iOS/Android.
const CACHE = 'maralavitch-v1';

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll([
      '/maralavitchstaff',
      '/worker.html',
    ])).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first strategy: always try network, fall back to cache
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(resp => {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
