// Retire service workers installed by older releases. Keep this URL available
// so returning browsers can unregister their existing worker.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", () => {
  self.registration.unregister().then(() =>
    self.clients.matchAll().then((clients) => {
      clients.forEach((client) => client.navigate(client.url));
    }),
  );
});
