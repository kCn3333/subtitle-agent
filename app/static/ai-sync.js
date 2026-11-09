const aiPanel=document.querySelector('#ai-sync-panel'),polishSelect=document.querySelector('#ai-polish');
const aiButtonLabel=document.querySelector('#ai-sync-button-label');
const aiButton=document.querySelector('#ai-sync-button'),aiMessage=document.querySelector('#ai-sync-status'),aiDownload=document.querySelector('#ai-download');
let aiQueued=false,aiQueuePosition=null,aiReferencePending=false,aiPreparedReference=null,aiRunning=false,aiJobId=null,aiMode='sync',aiRequestMode='sync',aiMediaTitle='Nieznany tytuł',aiChecking=true,aiPostPending=false,aiSeenOperation=null,aiObservedOperation=null,aiPhase='Oczekiwanie na odpowiedź modelu',aiStatusVersion=0,aiDirectory=false,aiDirectoryInputs=[],aiDirectoryChoices=new Map(),aiDirectoryVersion=0;
function aiEndpoint(mode=aiMode){return mode==='translation'?'ai-translate':'ai-sync'}
function aiHasInputs(){if(aiDirectory)return aiDirectoryInputs.some(item=>item.available);return aiMode==='translation'||polishSelect.options.length>0}
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
  aiMessage.textContent=aiQueued?`Oczekiwanie w kolejce AI${aiQueuePosition?` · pozycja ${aiQueuePosition}`:''}. Czas oczekiwania: ${elapsed} s.`:`AI przetwarza żądanie${'.'.repeat(elapsed%3+1)} Czas oczekiwania: ${elapsed} s.`;
}
function stopAiElapsed(){
  if(aiTimer!==null)clearInterval(aiTimer);
  aiTimer=null;aiStartedAt=null;aiRequestJobId=null;aiActivity.remove();
}
function aiControls(){
  aiButton.disabled=aiReferencePending||aiChecking||aiRunning||aiPostPending||!aiHasInputs()||(!aiDirectory&&referenceSelect.value!==aiPreparedReference);
  polishSelect.disabled=aiRunning||aiPostPending;
  referenceSelect.disabled=aiRunning||aiPostPending;
  document.querySelector('#rebuild').disabled=aiRunning||aiPostPending;
  if(aiDirectory)for(const select of document.querySelector('#ai-directory-inputs').querySelectorAll('select'))select.disabled=aiRunning||aiPostPending;
  aiButtonLabel.textContent=aiQueued?'Oczekuje w kolejce…':aiRunning||aiPostPending?'AI pracuje…':aiChecking?'Sprawdzanie stanu…':'Przekaż do AI';
}
function applyAiState(state,jobId,restoreInputs=false){
  if(jobId!==activeJobId||jobId!==aiJobId)return;
  aiChecking=false;
  if(aiDirectory&&state.batch&&(restoreInputs||['queued','running'].includes(state.status)))restoreDirectoryChoices(state.items||[]);
  if(state.batch&&typeof renderDirectoryAiProgress==='function')renderDirectoryAiProgress(state);
  if(['queued','running'].includes(state.status)){
    aiQueued=state.status==='queued';aiQueuePosition=state.queue_position;
    aiRunning=true;aiRequestJobId=jobId;aiRequestMode=state.mode;aiMediaTitle=state.title||aiMediaTitle;
    aiObservedOperation=state.operation_id;aiPhase=state.phase||'Oczekiwanie na odpowiedź modelu';
    const started=Date.parse(aiQueued?state.queued_at:state.started_at);aiStartedAt=Number.isFinite(started)?started:Date.now();
    if(state.polish_file&&polishSelect.options.length)polishSelect.value=state.polish_file;
    if(aiTimer===null)aiTimer=setInterval(updateAiElapsed,1000);
    aiDownload.hidden=true;updateAiElapsed();
  }else if(!aiPostPending){
    stopAiElapsed();aiRunning=false;aiQueued=false;aiQueuePosition=null;
    const operation=state.mode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
    if(state.status==='completed'||(state.batch&&state.result))showAiResult(state.result,jobId,state.mode);
    if(state.status==='failed'){
      if(!state.batch||!state.result)aiDownload.hidden=true;
      aiMessage.textContent=state.error?.message||'Operacja AI nie powiodła się';
    }
    if(state.operation_id&&state.operation_id!==aiSeenOperation){
      aiSeenOperation=state.operation_id;
      if(state.status==='failed'){
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
    applyAiState(state,jobId,loadResult);
    if(loadResult&&state.status==='idle'&&!aiPostPending){
      const mode=aiMode,result=await fetch(`/api/tasks/${jobId}/${aiEndpoint(mode)}`);
      if(result.ok){const saved=(await result.json()).result;if(saved?.batch)restoreDirectoryChoices(saved.outputs||[]);showAiResult(saved,jobId,mode);}
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
  if(result&&!result.batch&&mode==='sync'&&result.inputs?.[1]?.name!==polishSelect.value)return;
  if(result?.batch&&mode==='sync'&&(result.outputs||[]).some(item=>item.polish_file!==aiDirectoryChoices.get(item.job_id))){aiDownload.hidden=true;return;}
  aiDownload.hidden=!result;
  if(result){
    aiDownload.href=`/api/tasks/${jobId}/${aiEndpoint(mode)}/download`;
    if(result.filename)aiDownload.download=result.filename;
    document.querySelector('#ai-download-label').textContent=result.batch?'Pobierz wyniki ZIP':'Pobierz wynik SRT';
    aiMessage.textContent=result.batch?`Wyniki AI: ${result.file_count} plików SRT${result.failed_count?` · błędy: ${result.failed_count}`:''}. ZIP jest dostępny do pobrania.`:`Gotowe: ${result.cue_count} kwestii. Wynik SRT jest dostępny do pobrania.`;
  }
}
function renderAiSync(job){
  const report=job.report||{};
  document.querySelector('#ai-batch-progress').hidden=true;
  if(report.batch)return renderDirectoryHandoff(job);
  aiDirectory=false;document.querySelector('#ai-directory-inputs').hidden=true;
  document.querySelector('#ai-download-label').textContent='Pobierz wynik SRT';
  aiReferencePending=referenceNeedsConfirmation(report);
  aiPanel.hidden=!((job.status==='WORKPACK_READY'||(job.status==='WORKPACK_INCOMPLETE'&&aiReferencePending))&&['PREPARE_SYNC','PREPARE_TRANSLATION'].includes(report.pipeline)&&!report.requiresOcr);
  aiButton.hidden=aiPanel.hidden;
  if(aiPanel.hidden)return;
  aiMediaTitle=job.displayTitle||readableMediaTitle(report.media?.name||report.mediaInspection?.name||'');
  aiMode=report.pipeline==='PREPARE_TRANSLATION'?'translation':'sync';
  document.querySelector('#ai-panel-title').textContent=aiMode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
  document.querySelector('#ai-polish-field').hidden=aiMode==='translation';
  aiJobId=job.jobId;aiPreparedReference=(report.selectedReference||report.selectedEnglish)?sourceId(report.selectedReference||report.selectedEnglish):null;
  polishSelect.replaceChildren();
  for(const item of report.polishCandidates||[]){
    if(!item.archiveName?.toLowerCase().endsWith('.srt')||item.eligibleByDefault===false)continue;
    const option=document.createElement('option');option.value=item.archiveName;option.textContent=item.originalName||item.archiveName;polishSelect.append(option);
  }
  aiChecking=true;aiControls();
  aiMessage.textContent=aiReferencePending?'Najpierw zatwierdź wybór referencji.':aiRunning?(aiRequestJobId===job.jobId?'AI przetwarza żądanie…':'Poczekaj na zakończenie żądania AI poprzedniego zadania.'):'';aiDownload.hidden=true;
  refreshAiStatus(job.jobId,true);
}
referenceSelect.addEventListener('change',()=>{
  aiControls();
  if(!aiDirectory&&referenceSelect.value!==aiPreparedReference){aiMessage.textContent='Najpierw zbuduj workpack z wybraną referencją.';aiDownload.hidden=true}
});
polishSelect.addEventListener('change',()=>{aiDownload.hidden=true;aiMessage.textContent=''});
form.addEventListener('submit',hideAiHandoff);
document.querySelector('#rebuild').addEventListener('click',hideAiHandoff);
aiButton.addEventListener('click',async()=>{
  const jobId=aiJobId,mode=aiMode,title=aiMediaTitle;
  const operation=mode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
  if(!jobId||aiButton.disabled)return;
  aiPostPending=true;aiPhase='Wysyłanie danych i oczekiwanie na model';
  aiRunning=true;aiButton.disabled=true;polishSelect.disabled=true;aiDownload.hidden=true;
  aiControls();
  line('INFO',aiStage(mode),`${operation} · Film: ${title}`);
  aiRequestMode=mode;
  aiRequestJobId=jobId;aiStartedAt=Date.now();updateAiElapsed();aiTimer=setInterval(updateAiElapsed,1000);
  try{
    const payload=aiDirectory?directoryAiPayload():{reference_source_id:aiPreparedReference};
    if(!aiDirectory&&mode==='sync')payload.polish_file=polishSelect.value;
    const response=await fetch(`/api/tasks/${jobId}/${aiEndpoint(mode)}`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    const body=await response.json();
    if(response.status===409){aiPostPending=false;await refreshAiStatus(jobId);return}
    if(response.status===202){aiPostPending=false;applyAiState(body,jobId);return}
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

async function renderDirectoryHandoff(job){
  const report=job.report||{},version=++aiDirectoryVersion;
  document.querySelector('#ai-batch-progress').hidden=true;
  if(aiJobId!==job.jobId)aiDirectoryChoices=new Map();
  aiDirectory=true;aiJobId=job.jobId;aiReferencePending=false;aiPreparedReference=null;
  aiPanel.hidden=!(['WORKPACK_READY','WORKPACK_INCOMPLETE'].includes(job.status)&&['PREPARE_SYNC','PREPARE_TRANSLATION'].includes(report.mode)&&report.workpack);
  aiButton.hidden=aiPanel.hidden;
  if(aiPanel.hidden)return;
  aiMode=report.mode==='PREPARE_TRANSLATION'?'translation':'sync';
  aiMediaTitle=job.displayTitle;
  aiDirectoryInputs=[];aiChecking=true;aiControls();
  document.querySelector('#ai-panel-title').textContent=aiMode==='translation'?'Tłumaczenie katalogu przez AI':'Synchronizacja katalogu przez AI';
  document.querySelector('#ai-polish-field').hidden=true;
  document.querySelector('#ai-download-label').textContent='Pobierz wyniki ZIP';
  const container=document.querySelector('#ai-directory-inputs');container.hidden=false;container.replaceChildren();
  aiMessage.textContent='Sprawdzanie materiałów katalogu…';aiDownload.hidden=true;
  try{
    const response=await fetch(`/api/tasks/${job.jobId}/ai-inputs`);
    const body=await response.json();
    if(!response.ok)throw new Error(body.detail?.message||'Nie udało się odczytać materiałów AI');
    if(version!==aiDirectoryVersion||job.jobId!==activeJobId||job.jobId!==aiJobId)return;
    aiDirectoryInputs=body.items;
    for(const item of aiDirectoryInputs.filter(item=>item.available)){
      const candidates=item.polish_candidates||[];
      if(aiMode==='translation')continue;
      if(!candidates.some(entry=>entry.archiveName===aiDirectoryChoices.get(item.job_id)))aiDirectoryChoices.set(item.job_id,candidates[0]?.archiveName);
      if(candidates.length<=1)continue;
      const label=document.createElement('label'),name=document.createElement('span'),select=document.createElement('select');
      label.className='ai-batch-choice';name.textContent=item.name;select.dataset.jobId=item.job_id;
      for(const candidate of candidates){
        const option=document.createElement('option');option.value=candidate.archiveName;option.textContent=candidate.originalName;select.append(option);
      }
      select.value=aiDirectoryChoices.get(item.job_id);
      select.addEventListener('change',()=>{aiDirectoryChoices.set(item.job_id,select.value);aiDownload.hidden=true;aiMessage.textContent='Zmieniono wybór napisów PL. Przekaż katalog do AI, aby przygotować nowe wyniki.'});
      label.append(name,select);container.append(label);
    }
    const ready=aiDirectoryInputs.filter(item=>item.available).length;
    aiMessage.textContent=ready?`${ready} plików. Każdy będzie przetwarzany osobnym żądaniem. Pominięte lub niegotowe: ${body.items.length-ready}.`:'Brak gotowych materiałów do AI. Sprawdź raport plików.';
    await refreshAiStatus(job.jobId,true);
  }catch(error){if(version===aiDirectoryVersion&&job.jobId===activeJobId){aiMessage.textContent=error.message;aiChecking=false;aiControls()}}
}
function directoryAiPayload(){
  return {items:aiDirectoryInputs.filter(item=>item.available).map(item=>({job_id:item.job_id,
    reference_source_id:item.reference_source_id,polish_file:aiMode==='sync'?aiDirectoryChoices.get(item.job_id):null}))};
}

function restoreDirectoryChoices(rows){
  for(const row of rows){
    const input=aiDirectoryInputs.find(item=>item.job_id===row.job_id);
    if(row.polish_file&&input?.polish_candidates.some(item=>item.archiveName===row.polish_file))aiDirectoryChoices.set(row.job_id,row.polish_file);
  }
  for(const select of document.querySelector('#ai-directory-inputs').querySelectorAll('select'))select.value=aiDirectoryChoices.get(select.dataset.jobId);
}

// A saved job can finish loading before this script is available.
if(typeof activeDirectoryJob!=='undefined'&&activeDirectoryJob)renderAiSync(activeDirectoryJob);
