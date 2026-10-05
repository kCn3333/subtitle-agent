const aiPanel=document.querySelector('#ai-sync-panel'),polishSelect=document.querySelector('#ai-polish');
const aiButtonLabel=document.querySelector('#ai-sync-button-label');
const aiButton=document.querySelector('#ai-sync-button'),aiMessage=document.querySelector('#ai-sync-status'),aiDownload=document.querySelector('#ai-download');
let aiPreparedReference=null,aiRunning=false,aiJobId=null,aiMode='sync',aiRequestMode='sync';
function aiEndpoint(mode=aiMode){return mode==='translation'?'ai-translate':'ai-sync'}
function aiHasInputs(){return aiMode==='translation'||polishSelect.options.length>0}
function aiStage(mode=aiMode){return mode==='translation'?'AI_TRANSLATE':'AI_SYNC'}
let aiStartedAt=null,aiTimer=null,aiRequestJobId=null;
const aiActivity=document.createElement('div');
aiActivity.className='entry INFO';
function updateAiElapsed(){
  if(aiStartedAt===null)return;
  if(aiRequestJobId!==activeJobId){aiActivity.remove();return}
  const elapsed=Math.max(0,Math.floor((Date.now()-aiStartedAt)/1000));
  aiActivity.textContent=`[${aiStage(aiRequestMode)}] Żądanie do modelu w toku · oczekiwanie: ${Math.floor(elapsed/60)} min ${String(elapsed%60).padStart(2,'0')} s · API nie podaje procentowego postępu`;
  if(!output.contains(aiActivity))output.append(aiActivity);
  aiMessage.textContent=`AI przetwarza żądanie. Czas oczekiwania: ${elapsed} s.`;
}
function stopAiElapsed(){
  if(aiTimer!==null)clearInterval(aiTimer);
  aiTimer=null;aiStartedAt=null;aiRequestJobId=null;aiActivity.remove();
}
function hideAiHandoff(){aiPanel.hidden=true;aiButton.hidden=true}
function showAiResult(result,jobId,mode=aiMode){
  if(jobId!==activeJobId)return;
  if(mode!==aiMode)return;
  if(result&&mode==='sync'&&result.inputs?.[1]?.name!==polishSelect.value)return;
  aiDownload.hidden=!result;
  if(result){
    aiDownload.href=`/api/tasks/${jobId}/${aiEndpoint(mode)}/download`;
    aiMessage.textContent=`Gotowe: ${result.cue_count} kwestii. ${formatAiMetrics(result)}. ${formatAiCostSummary(result)}`;
  }
}
function renderAiSync(job){
  const report=job.report||{};
  aiPanel.hidden=!(job.status==='WORKPACK_READY'&&['PREPARE_SYNC','PREPARE_TRANSLATION'].includes(report.pipeline)&&!report.externalReferenceConfirmationRequired&&!report.requiresOcr);
  aiButton.hidden=aiPanel.hidden;
  if(aiPanel.hidden)return;
  aiMode=report.pipeline==='PREPARE_TRANSLATION'?'translation':'sync';
  document.querySelector('#ai-panel-title').textContent=aiMode==='translation'?'Tłumaczenie przez AI':'Synchronizacja przez AI';
  document.querySelector('#ai-polish-field').hidden=aiMode==='translation';
  const mode=aiMode;
  aiJobId=job.jobId;aiPreparedReference=sourceId(report.selectedEnglish);
  polishSelect.replaceChildren();
  for(const item of report.polishCandidates||[]){
    if(!item.archiveName?.toLowerCase().endsWith('.srt'))continue;
    const option=document.createElement('option');option.value=item.archiveName;option.textContent=item.originalName||item.archiveName;polishSelect.append(option);
  }
  aiButton.disabled=aiRunning||!aiHasInputs()||referenceSelect.value!==aiPreparedReference;
  aiMessage.textContent=aiRunning?(aiRequestJobId===job.jobId?'AI przetwarza żądanie…':'Poczekaj na zakończenie żądania AI poprzedniego zadania.'):'';aiDownload.hidden=true;
  fetch(`/api/tasks/${job.jobId}/${aiEndpoint(mode)}`).then(response=>response.ok?response.json():null)
    .then(body=>{if(!aiRunning)showAiResult(body?.result,job.jobId,mode)}).catch(()=>{});
}
referenceSelect.addEventListener('change',()=>{
  aiButton.disabled=aiRunning||!aiHasInputs()||referenceSelect.value!==aiPreparedReference;
  if(referenceSelect.value!==aiPreparedReference){aiMessage.textContent='Najpierw zbuduj workpack z wybraną referencją EN.';aiDownload.hidden=true}
});
polishSelect.addEventListener('change',()=>{aiDownload.hidden=true;aiMessage.textContent=''});
form.addEventListener('submit',hideAiHandoff);
document.querySelector('#rebuild').addEventListener('click',hideAiHandoff);
aiButton.addEventListener('click',async()=>{
  const jobId=aiJobId,mode=aiMode;
  if(!jobId||aiRunning)return;
  aiRunning=true;aiButton.disabled=true;polishSelect.disabled=true;aiDownload.hidden=true;
  aiButtonLabel.textContent='AI pracuje…';
  line('INFO',aiStage(mode),mode==='translation'?'Wysyłanie referencji EN do tłumaczenia na polski':'Wysyłanie referencji EN i istniejących napisów PL do skonfigurowanego API');
  aiRequestMode=mode;
  aiRequestJobId=jobId;aiStartedAt=Date.now();updateAiElapsed();aiTimer=setInterval(updateAiElapsed,1000);
  try{
    const payload={reference_source_id:aiPreparedReference};
    if(mode==='sync')payload.polish_file=polishSelect.value;
    const response=await fetch(`/api/tasks/${jobId}/${aiEndpoint(mode)}`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    const body=await response.json();
    stopAiElapsed();
    if(!response.ok){const error=new Error(body.detail?.message||'Operacja AI nie powiodła się');error.metrics=body.detail;throw error}
    if(jobId===activeJobId){showAiResult(body,jobId,mode);line('SUCCESS',aiStage(mode),`Zapisano SRT. ${formatAiMetrics(body)}. ${formatAiCostSummary(body)}`)}
  }catch(error){if(jobId===activeJobId){
    const metrics=error.metrics;
    const message=error.message+(metrics?.usage?` · ${formatAiMetrics(metrics)}. ${formatAiCostSummary(metrics)}`:'');
    aiMessage.textContent=message;line('ERROR',aiStage(mode),message);
  }}
  finally{stopAiElapsed();aiRunning=false;polishSelect.disabled=false;aiButtonLabel.textContent='Przekaż do AI';aiButton.disabled=!aiHasInputs()||referenceSelect.value!==aiPreparedReference}
});
