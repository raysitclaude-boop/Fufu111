const CACHE='fujifield-v4.0';
const ASSETS=['./','index.html','data.enc','manifest.json','apple-touch-icon.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
// network-first for data.enc (pick up nightly rebuilds when online), cache-first for the shell.
// Worker API calls (different origin) are never intercepted — they pass straight through.
self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  if(url.origin!==location.origin) return;                 // let write-proxy calls hit the network
  if(url.pathname.endsWith('data.enc')){
    e.respondWith(fetch(e.request).then(r=>{caches.open(CACHE).then(c=>c.put(e.request,r.clone()));return r}).catch(()=>caches.match(e.request)));
  }else{
    e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(n=>{caches.open(CACHE).then(c=>c.put(e.request,n.clone()));return n})));
  }
});
