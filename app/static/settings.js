const settingsForm=document.querySelector('#ai-settings'),settingsStatus=document.querySelector('#settings-status');
function displaySettings(settings){
  document.querySelector('#api-url').value=settings.api_url;
  document.querySelector('#model').value=settings.model;
  document.querySelector('#timeout').value=settings.timeout_seconds;
  document.querySelector('#reasoning-effort').value=settings.reasoning_effort??'';
  document.querySelector('#api-key').value='';
  document.querySelector('#clear-key').checked=false;
  document.querySelector('#key-status').textContent=settings.api_key_set?'Klucz API jest zapisany.':'Klucz API nie jest ustawiony.';
}
async function settingsRequest(url,options={}){
  const response=await fetch(url,options),body=await response.json();
  if(!response.ok)throw new Error(body.detail?.message||(Array.isArray(body.detail)?body.detail.map(item=>item.msg).join('; '):'Nie udało się wykonać żądania'));
  return body;
}
async function saveAiSettings(test){
  const buttons=settingsForm.querySelectorAll('button');buttons.forEach(button=>button.disabled=true);
  settingsStatus.textContent=test?'Zapisywanie i testowanie połączenia…':'Zapisywanie…';
  try{
    const payload={api_url:document.querySelector('#api-url').value,model:document.querySelector('#model').value,
      timeout_seconds:Number(document.querySelector('#timeout').value),
      reasoning_effort:document.querySelector('#reasoning-effort').value||null};
    const key=document.querySelector('#api-key').value;
    if(document.querySelector('#clear-key').checked)payload.api_key='';else if(key)payload.api_key=key;
    displaySettings(await settingsRequest('/api/settings/ai',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));
    if(test){const result=await settingsRequest('/api/settings/ai/test',{method:'POST'});settingsStatus.textContent=`Połączenie poprawne. Czas żądania: ${result.elapsed_seconds} s.`}
    else settingsStatus.textContent='Ustawienia zapisane.';
  }catch(error){settingsStatus.textContent=error.message}finally{buttons.forEach(button=>button.disabled=false)}
}
settingsForm.addEventListener('submit',event=>{event.preventDefault();saveAiSettings(false)});
document.querySelector('#test-api').addEventListener('click',()=>{if(settingsForm.reportValidity())saveAiSettings(true)});
settingsRequest('/api/settings/ai').then(displaySettings).catch(error=>settingsStatus.textContent=error.message);

const aiConsole=document.querySelector('#ai-console'),aiConsoleState=document.querySelector('#ai-console-state');
let consoleSignature=null,consoleBusy=false,consoleGeneration=0;
async function refreshAiConsole(){
  if(consoleBusy)return;
  consoleBusy=true;
  const generation=consoleGeneration;
  try{
    const body=await settingsRequest('/api/settings/ai/console');
    if(generation!==consoleGeneration)return;
    const entries=body.entries||[],signature=`${entries.length}:${entries.at(-1)?.id??0}`;
    if(signature!==consoleSignature){
      const atBottom=aiConsole.scrollHeight-aiConsole.scrollTop-aiConsole.clientHeight<40,scrollTop=aiConsole.scrollTop;
      aiConsole.replaceChildren();
      for(const entry of entries){
        const node=document.createElement('div');
        node.className=`entry ${['INFO','SUCCESS','ERROR'].includes(entry.level)?entry.level:'INFO'}`;
        node.textContent=`[${new Date(entry.timestamp).toLocaleString('pl-PL')}] [${entry.operation}] [${entry.level}]${entry.job_id?` [${entry.job_id}]`:''}\n${entry.message}\n`;
        aiConsole.append(node);
      }
      aiConsole.scrollTop=atBottom?aiConsole.scrollHeight:scrollTop;
      consoleSignature=signature;
    }
    aiConsoleState.textContent=entries.length?'Ostatnie 100 wpisów · odświeżanie co 2 s.':'Brak komunikatów. Wykonaj test połączenia lub synchronizację.';
  }catch(error){aiConsoleState.textContent=`Nie udało się odczytać konsoli: ${error.message}`}
  finally{consoleBusy=false}
}
document.querySelector('#refresh-ai-console').addEventListener('click',refreshAiConsole);
document.querySelector('#clear-ai-console').addEventListener('click',async()=>{
  consoleGeneration++;
  try{
    await settingsRequest('/api/settings/ai/console',{method:'DELETE'});
    aiConsole.replaceChildren();consoleSignature=null;aiConsoleState.textContent='Historia wyczyszczona.';
    await refreshAiConsole();
  }catch(error){aiConsoleState.textContent=error.message}
});
refreshAiConsole();setInterval(()=>{if(!document.hidden)refreshAiConsole()},2000);
