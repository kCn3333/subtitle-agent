async function refreshHealthBadge(id,url,name){
  const badge=document.querySelector(id);if(!badge)return;
  try{
    const response=await fetch(url);if(!response.ok)throw new Error();
    const state=await response.json();badge.className=`health ${state.available?'health-online':state.configured?'health-offline':'health-disabled'}`;
    badge.textContent=state.available?`● ${name} ONLINE`:state.configured?`● ${name} OFFLINE`:`● ${name} ${name==='API'?'NIESKONFIGUROWANE':'WYŁĄCZONY'}`;
    badge.title=state.message||'';
  }catch{badge.className='health health-offline';badge.textContent=`● ${name} OFFLINE`;badge.title='Brak odpowiedzi serwera'}
}
async function refreshHeaderHealth(){
  await Promise.all([refreshHealthBadge('#ocr-health','/api/workpacks/ocr-health','OCR'),
    refreshHealthBadge('#api-health','/api/settings/ai/health','API')]);
}
refreshHeaderHealth();setInterval(()=>{if(!document.hidden)refreshHeaderHealth()},30000);
