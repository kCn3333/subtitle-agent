async function refreshHealthBadge(id,url,name){
  const badge=document.querySelector(id);if(!badge)return;
  try{
    const response=await fetch(url);if(!response.ok)throw new Error();
    const state=await response.json();badge.className=`health ${state.available?'health-online':state.configured?'health-offline':'health-disabled'}`;
    badge.textContent=state.available?`● ${name} ONLINE`:state.configured?`● ${name} OFFLINE`:`● ${name} WYŁĄCZONY`;
    badge.title=state.message||'';
  }catch{badge.className='health health-offline';badge.textContent=`● ${name} OFFLINE`;badge.title='Brak odpowiedzi serwera'}
}
async function refreshHeaderHealth(){
  await refreshHealthBadge('#ocr-health','/api/workpacks/ocr-health','OCR');
}
refreshHeaderHealth();setInterval(()=>{if(!document.hidden)refreshHeaderHealth()},30000);

const navToggle=document.querySelector('#nav-toggle'),appNavigation=document.querySelector('#app-navigation');
if(navToggle&&appNavigation){
  function setMenuOpen(open){
    appNavigation.classList.toggle('is-open',open);
    navToggle.setAttribute('aria-expanded',String(open));
    navToggle.setAttribute('aria-label',open?'Zamknij menu':'Otwórz menu');
  }
  navToggle.addEventListener('click',()=>setMenuOpen(navToggle.getAttribute('aria-expanded')!=='true'));
  appNavigation.addEventListener('click',event=>{if(event.target.closest('a'))setMenuOpen(false)});
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&navToggle.getAttribute('aria-expanded')==='true'){
      setMenuOpen(false);navToggle.focus();
    }
  });
  document.addEventListener('click',event=>{
    if(!navToggle.contains(event.target)&&!appNavigation.contains(event.target))setMenuOpen(false);
  });
  window.matchMedia('(max-width: 700px)').addEventListener('change',()=>setMenuOpen(false));
}
