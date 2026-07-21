const CACHE='fujifield-v5.2';
const ASSETS=['./','index.html','data.enc','dataw.enc','manifest.json','apple-touch-icon.png'];
// allSettled: dataw.enc may not exist until the first V5 nightly build — don't brick install on a 404
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>Promise.allSettled(ASSETS.map(a=>c.add(a)))).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
// NETWORK-FIRST for the app shell (index.html) and *.enc, so an uploaded app
// update or nightly rebuild is picked up on the next online launch. Previously
// index.html was cache-first, which froze the app on the version cached at
// install time — data refreshed but the UI never changed. Cache is still the
// fallback, so offline use is unaffected.
// Worker API calls (different origin) are never intercepted — they pass straight through.
const netFirst=e=>e.respondWith(
  fetch(e.request).then(r=>{if(r.ok)caches.open(CACHE).then(c=>c.put(e.request,r.clone()));return r})
                  .catch(()=>caches.match(e.request).then(r=>r||caches.match('index.html'))));
self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  if(url.origin!==location.origin) return;                 // let write-proxy calls hit the network
  const p=url.pathname;
  if(e.request.mode==='navigate'||p.endsWith('.enc')||p.endsWith('/')||p.endsWith('index.html')||p.endsWith('sw.js')){
    netFirst(e);
  }else{
    e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(n=>{if(n.ok)caches.open(CACHE).then(c=>c.put(e.request,n.clone()));return n})));
  }
});
