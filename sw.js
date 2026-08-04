// Mar-A-Lavitch Staff Service Worker
// Enables PWA install prompts and "Add to Home Screen" on iOS/Android.
const CACHE = 'maralavitch-v3';

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll([
      '/maralavitchstaff',
      '/worker.html',
      '/pos-shared.js',
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

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const path = new URL(e.request.url).pathname;

  // API responses are live state — orders, punches, who's clocked in. Serving
  // a cached copy when the network fails would present old data as fresh with
  // no way for the app to tell. Let those requests fail honestly; the pages
  // know how to show "can't reach the kitchen" — they can't know "this answer
  // is from an hour ago".
  if (path.startsWith('/api/') || path.startsWith('/photos/')) return;

  // The app shell is network-first with cache fallback, so the app still
  // opens on flaky WiFi but picks up deployed changes as soon as it can.
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
