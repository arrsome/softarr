/**
 * Softarr service worker.
 *
 * Strategy:
 *   - Shell assets (CSS, JS, logo, fonts): cache-first
 *   - API calls: network-only (never cache; data must be fresh)
 *   - HTML pages: network-first with offline fallback
 */

const CACHE_NAME = 'softarr-shell-v1';

const SHELL_ASSETS = [
    '/static/css/styles.css',
    '/static/js/app.js',
    '/static/js/tooltips.js',
    '/static/images/logo.svg',
];

// ---------------------------------------------------------------------------
// Install -- pre-cache shell assets
// ---------------------------------------------------------------------------
self.addEventListener('install', function (event) {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            // Cache individually so a single failure does not abort installation
            return Promise.allSettled(
                SHELL_ASSETS.map(function (url) {
                    return cache.add(url).catch(function () {});
                })
            );
        })
    );
});

// ---------------------------------------------------------------------------
// Activate -- purge old caches
// ---------------------------------------------------------------------------
self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys
                    .filter(function (key) { return key !== CACHE_NAME; })
                    .map(function (key) { return caches.delete(key); })
            );
        }).then(function () {
            return self.clients.claim();
        })
    );
});

// ---------------------------------------------------------------------------
// Fetch -- routing logic
// ---------------------------------------------------------------------------
self.addEventListener('fetch', function (event) {
    var url = new URL(event.request.url);

    // Never intercept: API calls, auth routes, WebSocket upgrades
    if (
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/auth/') ||
        event.request.headers.get('upgrade') === 'websocket'
    ) {
        return; // Let browser handle natively
    }

    // Shell assets: cache-first
    if (SHELL_ASSETS.includes(url.pathname)) {
        event.respondWith(
            caches.match(event.request).then(function (cached) {
                return cached || fetch(event.request).then(function (response) {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(event.request, clone);
                    });
                    return response;
                });
            })
        );
        return;
    }

    // HTML pages: network-first, no offline fallback (app requires auth/server)
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(function () {
                // Return a minimal offline notice if network is unreachable
                return new Response(
                    '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Softarr - Offline</title>' +
                    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
                    '<style>body{font-family:system-ui;background:#1a1a1a;color:#c0c0c0;display:flex;' +
                    'align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}' +
                    'h1{color:#fff;font-size:1.25rem}p{font-size:0.875rem;color:#888}</style></head>' +
                    '<body><div><h1>Softarr</h1><p>You are offline. Please check your connection.</p></div></body></html>',
                    { headers: { 'Content-Type': 'text/html' } }
                );
            })
        );
        return;
    }
});

// ---------------------------------------------------------------------------
// Push notifications
// ---------------------------------------------------------------------------
self.addEventListener('push', function (event) {
    var data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: 'Softarr', body: event.data ? event.data.text() : '' };
    }

    var title = data.title || 'Softarr';
    var options = {
        body: data.body || '',
        icon: '/static/images/logo.svg',
        badge: '/static/images/logo.svg',
        tag: data.tag || 'softarr',
        data: { url: data.url || '/' },
        requireInteraction: data.requireInteraction || false,
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    var targetUrl = (event.notification.data && event.notification.data.url) || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            for (var i = 0; i < clientList.length; i++) {
                var client = clientList[i];
                if (client.url === targetUrl && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
