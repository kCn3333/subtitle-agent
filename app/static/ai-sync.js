const aiPanel=document.querySelector('#ai-sync-panel'),polishSelect=document.querySelector('#ai-polish');
const aiButtonLabel=document.querySelector('#ai-sync-button-label');
const aiButton=document.querySelector('#ai-sync-button'),aiMessage=document.querySelector('#ai-sync-status'),aiDownload=document.querySelector('#ai-download');
let aiPreparedReference=null,aiRunning=false,aiJobId=null,aiMode='sync',aiRequestMode='sync',aiMediaTitle='Nieznany tytuł',aiChecking=true,aiPostPending=false,aiSeenOperation=null,aiObservedOperation=null,aiPhase='Oczekiwanie na odpowiedź modelu',aiStatusVersion=0;
function aiEndpoint(mode=aiMode){return mode==='translation'?'ai-translate':'ai-sync'}
function aiHasInputs(){return aiMode==='translation'||polishSelect.options.length>0}
function aiStage(mode=aiMode){return mode==='translation'?'AI_TRANSLATE':'AI_SYNC'}
let aiStartedAt=null,aiTimer=null,aiRequestJobId=null;
const aiActivity=document.createElement('div');
aiActivity.className='entry INFO ai-console-activity';
const aiSpinner=document.createElement('span'),aiActivityText=document.createElement('span');
aiSpinner.className='ai-spinner';aiSpinner.setAttribute('aria-hidden','true');
aiActivity.append(aiSpinner,aiActivityText);
aiActivity.setAttribute('aria-live','off');
function updateAiElapsed(){
  if(aiStartedAt===null)return;
  if(aiRequestJobId!==activeJobId){aiActivity.remove();return}
  const elapsed=Math.max(0,Math.floor((Date.now()-aiStartedAt)/1000));
  aiActivityText.textContent=`[${aiStage(aiRequestMode)}] ${aiPhase} · Film: ${aiMediaTitle} · oczekiwanie: ${Math.floor(elapsed/60)} min ${String(elapsed%60).padStart(2,'0')} s`;
  if(!output.contains(aiActivity))output.append(aiActivity);
  aiMessage.textContent=`AI przetwarza żądanie${'.'.repeat(elapsed%3+1)} Czas oczekiwania: ${elapsed} s.`;
}
function stopAiElapsed(){
  if(aiTimer!==null)clearInterval(aiTimer);
  aiTimer=null;aiStartedAt=null;aiRequestJobId=null;aiActivity.remove();
}
function aiControls(){
  aiButton.disabled=aiChecking||aiRunning||aiPostPending||!aiHasInputs()||referenceSelect.value!==aiPreparedReference;
  polishSelect.disabled=aiRunning||aiPostPending;
  referenceSelect.disabled=aiRunning||aiPostPending;
  document.querySelector('#rebuild').disabled=aiRunning||aiPostPending;
  aiButtonLabel.textContent=aiRunning||aiPostPending?'AI pracuje…':aiChecking?'Sprawdzanie stanu…':'Przekaż do AI';
}
function applyAiState(state,jobId){
  if(jobId!==activeJobId||jobId!==aiJobId)return;
  aiChecking=false;
  if(state.status==='running'){
    aiRunning=true;aiRequestJobId=jobId;aiRequestMode=state.mode;aiMediaTitle=state.title||aiMediaTitle;
    aiObservedOperation=state.operation_id;aiPhase=state.phase||'Oczekiwanie na odpowiedź modelu';
    const started=Date.parse(state.started_at);aiStartedAt=Number.isFinite(started)?started:Date.now();
    if(state.polish_file&&polishSelect.options.length)polishSelect.value=state.polish_file;
    if(aiTimer===null)aiTimer=setInterval(updateAiElapsed,1000);
    aiDownload.hidden=true;updateAiElapsed();
  }else if(!aiPostPending){
    stopAiElapsed();aiRunning=false;
    const operation=state.mode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
    if(state.status==='completed')showAiResult(state.result,jobId,state.mode);
    if(state.operation_id&&state.operation_id!==aiSeenOperation){
      aiSeenOperation=state.operation_id;
      if(state.status==='failed'){
        aiDownload.hidden=true;aiMessage.textContent=state.error?.message||'Operacja AI nie powiodła się';
        line('ERROR',aiStage(state.mode),aiMessage.textContent);
      }
      if(['completed','failed'].includes(state.status))line(state.status==='completed'?'SUCCESS':'ERROR',`${aiStage(state.mode)}_SUMMARY`,
        formatAiOperationSummary(state.result||state.error||{},`${operation} · Film: ${state.title||aiMediaTitle}`));
    }
  }
  aiControls();
}
async function refreshAiStatus(jobId=aiJobId,loadResult=false){
  if(!jobId||jobId!==activeJobId)return;
  const version=++aiStatusVersion;
  try{
    const response=await fetch(`/api/tasks/${jobId}/ai-status`);
    if(!response.ok)throw new Error('Nie udało się sprawdzić stanu AI');
    const state=await response.json();
    if(version!==aiStatusVersion||jobId!==aiJobId||jobId!==activeJobId)return;
    if(loadResult&&state.polish_file&&polishSelect.options.length)polishSelect.value=state.polish_file;
    applyAiState(state,jobId);
    if(loadResult&&state.status==='idle'&&!aiPostPending){
      const mode=aiMode,result=await fetch(`/api/tasks/${jobId}/${aiEndpoint(mode)}`);
      if(result.ok)showAiResult((await result.json()).result,jobId,mode);
    }
  }catch{
    if(version!==aiStatusVersion||jobId!==activeJobId||jobId!==aiJobId)return;
    aiChecking=true;aiControls();
    aiMessage.textContent='Nie można sprawdzić stanu AI. Ponawiam sprawdzenie…';
  }
}
setInterval(()=>{if(aiJobId===activeJobId&&!aiPanel.hidden)refreshAiStatus()},2000);
function hideAiHandoff(){aiPanel.hidden=true;aiButton.hidden=true}
function showAiResult(result,jobId,mode=aiMode){
  if(jobId!==activeJobId)return;
  if(mode!==aiMode)return;
  if(result&&mode==='sync'&&result.inputs?.[1]?.name!==polishSelect.value)return;
  aiDownload.hidden=!result;
  if(result){
    aiDownload.href=`/api/tasks/${jobId}/${aiEndpoint(mode)}/download`;
    aiMessage.textContent=`Gotowe: ${result.cue_count} kwestii. Wynik SRT jest dostępny do pobrania.`;
  }
}
function renderAiSync(job){
  const report=job.report||{};
  aiPanel.hidden=!(job.status==='WORKPACK_READY'&&['PREPARE_SYNC','PREPARE_TRANSLATION'].includes(report.pipeline)&&!report.externalReferenceConfirmationRequired&&!report.requiresOcr);
  aiButton.hidden=aiPanel.hidden;
  if(aiPanel.hidden)return;
  aiMediaTitle=job.displayTitle||readableMediaTitle(report.media?.name||report.mediaInspection?.name||'');
  aiMode=report.pipeline==='PREPARE_TRANSLATION'?'translation':'sync';
  document.querySelector('#ai-panel-title').textContent=aiMode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
  document.querySelector('#ai-polish-field').hidden=aiMode==='translation';
  aiJobId=job.jobId;aiPreparedReference=sourceId(report.selectedEnglish);
  polishSelect.replaceChildren();
  for(const item of report.polishCandidates||[]){
    if(!item.archiveName?.toLowerCase().endsWith('.srt'))continue;
    const option=document.createElement('option');option.value=item.archiveName;option.textContent=item.originalName||item.archiveName;polishSelect.append(option);
  }
  aiChecking=true;aiControls();
  aiMessage.textContent=aiRunning?(aiRequestJobId===job.jobId?'AI przetwarza żądanie…':'Poczekaj na zakończenie żądania AI poprzedniego zadania.'):'';aiDownload.hidden=true;
  refreshAiStatus(job.jobId,true);
}
referenceSelect.addEventListener('change',()=>{
  aiControls();
  if(referenceSelect.value!==aiPreparedReference){aiMessage.textContent='Najpierw zbuduj workpack z wybraną referencją EN.';aiDownload.hidden=true}
});
polishSelect.addEventListener('change',()=>{aiDownload.hidden=true;aiMessage.textContent=''});
form.addEventListener('submit',hideAiHandoff);
document.querySelector('#rebuild').addEventListener('click',hideAiHandoff);
aiButton.addEventListener('click',async()=>{
  const jobId=aiJobId,mode=aiMode,title=aiMediaTitle;
  const operation=mode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
  if(!jobId||aiRunning||aiChecking||aiPostPending)return;
  aiPostPending=true;aiPhase='Wysyłanie danych i oczekiwanie na model';
  aiRunning=true;aiButton.disabled=true;polishSelect.disabled=true;aiDownload.hidden=true;
  aiControls();
  line('INFO',aiStage(mode),`${operation} · Film: ${title}`);
  aiRequestMode=mode;
  aiRequestJobId=jobId;aiStartedAt=Date.now();updateAiElapsed();aiTimer=setInterval(updateAiElapsed,1000);
  try{
    const payload={reference_source_id:aiPreparedReference};
    if(mode==='sync')payload.polish_file=polishSelect.value;
    const response=await fetch(`/api/tasks/${jobId}/${aiEndpoint(mode)}`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    const body=await response.json();
    if(response.status===409){aiPostPending=false;await refreshAiStatus(jobId);return}
    stopAiElapsed();
    aiSeenOperation=body.operation_id||body.detail?.operation_id||aiObservedOperation;
    if(!response.ok){const error=new Error(body.detail?.message||'Operacja AI nie powiodła się');error.metrics=body.detail;throw error}
    if(jobId===activeJobId){showAiResult(body,jobId,mode);line('SUCCESS',`${aiStage(mode)}_SUMMARY`,formatAiOperationSummary(body,`${operation} · Film: ${title}`))}
  }catch(error){if(jobId===activeJobId){
    const metrics=error.metrics;
    aiMessage.textContent=error.message;line('ERROR',aiStage(mode),error.message);
    if(metrics)line('ERROR',`${aiStage(mode)}_SUMMARY`,formatAiOperationSummary(metrics,`${operation} · Film: ${title}`));
  }}
  finally{aiPostPending=false;await refreshAiStatus(jobId);aiControls()}
});

aiDownload.addEventListener('click',()=>line('INFO','DOWNLOAD',`Pobieranie wyniku AI · Film: ${aiMediaTitle}`));
