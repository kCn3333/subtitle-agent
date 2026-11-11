// Display/click handling only: no push, fetch interception or background task polling.
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil((async()=>{
    const url=new URL(event.notification.data?.url||'/',self.location.origin);
    if(url.origin!==self.location.origin||!['/','/settings'].includes(url.pathname))return;
    const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const client=clients.find(client=>new URL(client.url).origin===self.location.origin);
    if(client){await client.navigate(url.href);await client.focus()}
    else await self.clients.openWindow(url.href);
  })());
});
