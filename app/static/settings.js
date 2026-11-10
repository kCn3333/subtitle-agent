const settingsForm=document.querySelector('#ai-settings'),settingsStatus=document.querySelector('#settings-status');
const savedModelSelect=document.querySelector('#saved-model');
let apiKeySet=false,settingsBusy=false,selectedModelId=null,keySourceId='active',keyScopeUrl='',keyAvailable=false,savedModels=[];
function displayKeyState(settings){
  apiKeySet=Boolean(settings.api_key_set);
  document.querySelector('#api-key').placeholder=apiKeySet?'••••••••':'Wpisz klucz API';
  document.querySelector('#clear-key').disabled=settingsBusy||!apiKeySet;
  document.querySelector('#key-status').textContent=apiKeySet?'Klucz API jest dostępny dla tej konfiguracji. Puste pole zachowuje klucz.':'Klucz API nie jest ustawiony.';
}
function setSettingsBusy(busy){
  settingsBusy=busy;
  settingsForm.querySelectorAll('button').forEach(button=>button.disabled=busy);
  savedModelSelect.disabled=busy;
  document.querySelector('#clear-key').disabled=busy||!apiKeySet;
  document.querySelector('#delete-model').disabled=busy||!selectedModelId;
}
function displaySettings(settings){
  document.querySelector('#api-url').value=settings.api_url;
  document.querySelector('#model').value=settings.model;
  document.querySelector('#timeout').value=settings.timeout_seconds;
  document.querySelector('#reasoning-effort').value=settings.reasoning_effort??'';
  document.querySelector('#max-output-tokens').value=settings.max_output_tokens??'';
  document.querySelector('#response-format').value=settings.response_format??'';
  document.querySelector('#api-key').value='';
  keyScopeUrl=settings.api_url;keyAvailable=Boolean(settings.api_key_set);
  displayKeyState(settings);
}
function modelDraft(){
  const settings={api_url:document.querySelector('#api-url').value,model:document.querySelector('#model').value,
    timeout_seconds:Number(document.querySelector('#timeout').value),reasoning_effort:document.querySelector('#reasoning-effort').value||null,
    max_output_tokens:document.querySelector('#max-output-tokens').value?Number(document.querySelector('#max-output-tokens').value):null,
    response_format:document.querySelector('#response-format').value||null};
  const key=document.querySelector('#api-key').value;if(key)settings.api_key=key;
  return {settings,key_source_id:keySourceId,model_id:selectedModelId};
}
function jsonRequest(body){return {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}}
async function refreshModels(preferred){
  const body=await settingsRequest('/api/settings/ai/models');savedModels=body.models||[];
  selectedModelId=preferred===undefined?body.active_id||null:preferred;
  savedModelSelect.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='Nowy model / konfiguracja tymczasowa';savedModelSelect.append(empty);
  for(const model of savedModels){const option=document.createElement('option');option.value=model.id;option.textContent=`${model.model} · ${model.api_url}`;savedModelSelect.append(option)}
  savedModelSelect.value=selectedModelId||'';
  document.querySelector('#saved-model-count').textContent=`Zapisane modele: ${savedModels.length}/${body.limit||10}. Limit zmienisz w Ustawieniach.`;
  document.querySelector('#delete-model').disabled=settingsBusy||!selectedModelId;
}
async function waitForAiTest(state){
  while(['queued','running'].includes(state.status)){
    settingsStatus.textContent=state.status==='queued'?`Test oczekuje w kolejce AI${state.queue_position?` · pozycja ${state.queue_position}`:''}…`:'Testowanie połączenia…';
    await new Promise(resolve=>setTimeout(resolve,1500));state=await settingsRequest(`/api/ai-operations/${state.operation_id}`);
  }
  if(state.status==='failed')throw new Error(state.error?.message||'Test połączenia nie powiódł się.');return state;
}
async function saveAiSettings(test){
  setSettingsBusy(true);settingsStatus.textContent=test?'Testowanie konfiguracji…':'Aktywowanie konfiguracji…';
  try{
    if(test){await waitForAiTest(await settingsRequest('/api/settings/ai/test',jsonRequest(modelDraft())));settingsStatus.textContent='Połączenie poprawne.'}
    else{
      displaySettings(await settingsRequest('/api/settings/ai/activate',jsonRequest(modelDraft())));
      keySourceId='active';await refreshModels(null);
      if(typeof refreshHeaderHealth==='function')refreshHeaderHealth();
      settingsStatus.textContent='Konfiguracja aktywna bez zapisu na liście. Obowiązuje do restartu aplikacji lub wyboru innego modelu.';
    }
  }catch(error){settingsStatus.textContent=error.message}finally{setSettingsBusy(false)}
}
settingsForm.addEventListener('submit',event=>{event.preventDefault();if(settingsForm.reportValidity())return saveAiSettings(false)});
document.querySelector('#test-api').addEventListener('click',()=>{if(settingsForm.reportValidity())return saveAiSettings(true)});
document.querySelector('#save-model').addEventListener('click',async()=>{
  if(!settingsForm.reportValidity())return;setSettingsBusy(true);
  try{
    const result=await settingsRequest('/api/settings/ai/models',jsonRequest(modelDraft()));
    displaySettings(result.settings);keySourceId=result.id;await refreshModels(result.id);
    if(typeof refreshHeaderHealth==='function')refreshHeaderHealth();settingsStatus.textContent='Model zapisany na liście i aktywny.';
  }catch(error){settingsStatus.textContent=error.message}finally{setSettingsBusy(false)}
});
savedModelSelect.addEventListener('change',async()=>{
  const modelId=savedModelSelect.value;
  if(!modelId){selectedModelId=null;keySourceId=null;displaySettings({api_url:'',model:'',timeout_seconds:120});setSettingsBusy(false);settingsStatus.textContent='Wpisz konfigurację. Możesz ją przetestować lub użyć bez zapisywania.';return}
  setSettingsBusy(true);
  try{
    const settings=await settingsRequest(`/api/settings/ai/models/${encodeURIComponent(modelId)}/activate`,{method:'POST'});
    selectedModelId=modelId;keySourceId=modelId;displaySettings(settings);
    if(typeof refreshHeaderHealth==='function')refreshHeaderHealth();settingsStatus.textContent='Wybrany model jest aktywny.';
  }catch(error){savedModelSelect.value=selectedModelId||'';settingsStatus.textContent=error.message}finally{setSettingsBusy(false)}
});
document.querySelector('#delete-model').addEventListener('click',async()=>{
  if(!selectedModelId)return;setSettingsBusy(true);
  try{
    await settingsRequest(`/api/settings/ai/models/${encodeURIComponent(selectedModelId)}`,{method:'DELETE'});
    keySourceId='active';await refreshModels(null);settingsStatus.textContent='Model usunięty z listy. Aktywna konfiguracja pozostaje bez zmian.';
  }catch(error){settingsStatus.textContent=error.message}finally{setSettingsBusy(false)}
});
document.querySelector('#api-url').addEventListener('input',()=>displayKeyState({api_key_set:keyAvailable&&document.querySelector('#api-url').value.trim().replace(/\/+$/,'')===keyScopeUrl}));
document.querySelector('#clear-key').addEventListener('click',async()=>{
  if(!apiKeySet)return;setSettingsBusy(true);
  try{
    const base=keySourceId&&keySourceId!=='active'?savedModels.find(model=>model.id===keySourceId):await settingsRequest('/api/settings/ai');
    const settings={...base,api_key:''};delete settings.id;delete settings.api_key_set;
    const draft={settings,key_source_id:keySourceId,model_id:selectedModelId};
    if(selectedModelId){await settingsRequest('/api/settings/ai/models',jsonRequest(draft));await refreshModels(selectedModelId)}
    else await settingsRequest('/api/settings/ai/activate',jsonRequest(draft));
    document.querySelector('#api-key').value='';keyAvailable=false;displayKeyState({api_key_set:false});
    if(typeof refreshHeaderHealth==='function')refreshHeaderHealth();settingsStatus.textContent='Klucz API został usunięty.';
  }catch(error){settingsStatus.textContent=error.message}finally{setSettingsBusy(false)}
});
settingsRequest('/api/settings/ai').then(async settings=>{
  displaySettings(settings);await refreshModels();keySourceId=selectedModelId||'active';
}).catch(error=>settingsStatus.textContent=error.message);

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
        node.className=`entry ${['INFO','SUCCESS','ERROR','SUMMARY'].includes(entry.level)?entry.level:'INFO'}`;
        node.textContent=`[${new Date(entry.timestamp).toLocaleString('pl-PL')}] [${entry.operation}] [${entry.level}]${entry.job_id?` [${entry.job_id}]`:''}\n${entry.message}\n`;
        aiConsole.append(node);
      }
      aiConsole.scrollTop=atBottom?aiConsole.scrollHeight:scrollTop;
      consoleSignature=signature;
    }
    aiConsoleState.textContent='';
  }catch(error){aiConsoleState.textContent=`Nie udało się odczytać konsoli: ${error.message}`}
  finally{consoleBusy=false}
}
document.querySelector('#clear-ai-console').addEventListener('click',async()=>{
  consoleGeneration++;
  try{
    await settingsRequest('/api/settings/ai/console',{method:'DELETE'});
    aiConsole.replaceChildren();consoleSignature=null;aiConsoleState.textContent='Historia wyczyszczona.';
    await refreshAiConsole();
  }catch(error){aiConsoleState.textContent=error.message}
});
refreshAiConsole();setInterval(()=>{if(!document.hidden)refreshAiConsole()},2000);
