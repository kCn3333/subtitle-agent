(() => {
  const csrf=document.querySelector('meta[name="csrf-token"]')?.content;
  const originalFetch=window.fetch.bind(window);
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'||input instanceof URL?input:input.url,window.location.href);
    const method=(options.method||input.method||'GET').toUpperCase();
    const headers=new Headers(options.headers||input.headers);
    if(url.origin===window.location.origin&& !['GET','HEAD','OPTIONS'].includes(method)&&csrf)headers.set('X-CSRF-Token',csrf);
    const response=await originalFetch(input,{...options,headers});
    if(url.origin===window.location.origin&&url.pathname.startsWith('/api/')&&response.status===401)window.location.replace('/login');
    return response;
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const logout=document.querySelector('#logout');
    logout?.addEventListener('click',async()=>{
      logout.disabled=true;
      try{
        const response=await fetch('/api/auth/logout',{method:'POST'});
        if(response.ok||response.status===401)window.location.replace('/login');
        else{logout.disabled=false;logout.textContent='Wyloguj — spróbuj ponownie'}
      }catch{logout.disabled=false;logout.textContent='Wyloguj — spróbuj ponownie'}
    });
  });
})();
