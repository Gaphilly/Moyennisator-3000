// sw.js
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open('m3000-v1').then(cache => cache.addAll([
      '/static/favicon.png'
    ]))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(resp => resp || fetch(event.request))
  );
});
