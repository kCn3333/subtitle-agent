const aiPanel=document.querySelector('#ai-sync-panel'),polishSelect=document.querySelector('#ai-polish');
const aiButton=document.querySelector('#ai-sync-button'),aiMessage=document.querySelector('#ai-sync-status'),aiDownload=document.querySelector('#ai-download');
let aiPreparedReference=null,aiRunning=false,aiJobId=null;
let aiStartedAt=null,aiTimer=null,aiRequestJobId=null;
const aiActivity=document.createElement('div');
aiActivity.className='entry INFO';
function updateAiElapsed(){
  if(aiStartedAt===null)return;
  if(aiRequestJobId!==activeJobId){aiActivity.remove();return}
  const elapsed=Math.max(0,Math.floor((Date.now()-aiStartedAt)/1000));
  aiActivity.textContent=`[AI_SYNC] Żądanie do modelu w toku · oczekiwanie: ${Math.floor(elapsed/60)} min ${String(elapsed%60).padStart(2,'0')} s · API nie podaje procentowego postępu`;
  if(!output.contains(aiActivity))output.append(aiActivity);
  aiMessage.textContent=`AI przetwarza żądanie. Czas oczekiwania: ${elapsed} s.`;
}
function stopAiElapsed(){
  if(aiTimer!==null)clearInterval(aiTimer);
  aiTimer=null;aiStartedAt=null;aiRequestJobId=null;aiActivity.remove();
}
function hideAiHandoff(){aiPanel.hidden=true;aiButton.hidden=true}
function showAiResult(result,jobId){
  if(jobId!==activeJobId)return;
  if(result&&result.inputs?.[1]?.name!==polishSelect.value)return;
  aiDownload.hidden=!result;
  if(result){
    aiDownload.href=`/api/tasks/${jobId}/ai-sync/download`;
    const usage=result.usage;
    aiMessage.textContent=`Gotowe: ${result.cue_count} kwestii. Czas żądania: ${result.elapsed_seconds} s.${usage?` Tokeny: wejście ${usage.prompt_tokens??'—'}, wyjście ${usage.completion_tokens??'—'}, razem ${usage.total_tokens??'—'}.`:''}`;
  }
}
function renderAiSync(job){
  const report=job.report||{};
  aiPanel.hidden=!(job.status==='WORKPACK_READY'&&report.pipeline==='PREPARE_SYNC'&&!report.externalReferenceConfirmationRequired&&!report.requiresOcr);
  aiButton.hidden=aiPanel.hidden;
  if(aiPanel.hidden)return;
  aiJobId=job.jobId;aiPreparedReference=sourceId(report.selectedEnglish);
  polishSelect.replaceChildren();
  for(const item of report.polishCandidates||[]){
    if(!item.archiveName?.toLowerCase().endsWith('.srt'))continue;
    const option=document.createElement('option');option.value=item.archiveName;option.textContent=item.originalName||item.archiveName;polishSelect.append(option);
  }
  aiButton.disabled=aiRunning||!polishSelect.options.length||referenceSelect.value!==aiPreparedReference;
  aiMessage.textContent=aiRunning?(aiRequestJobId===job.jobId?'AI przetwarza żądanie…':'Poczekaj na zakończenie żądania AI poprzedniego zadania.'):'Workpack gotowy. Wybierz PL i kliknij „Przekaż do AI”.';aiDownload.hidden=true;
  fetch(`/api/tasks/${job.jobId}/ai-sync`).then(response=>response.ok?response.json():null)
    .then(body=>{if(!aiRunning)showAiResult(body?.result,job.jobId)}).catch(()=>{});
}
referenceSelect.addEventListener('change',()=>{
  aiButton.disabled=aiRunning||!polishSelect.options.length||referenceSelect.value!==aiPreparedReference;
  if(referenceSelect.value!==aiPreparedReference){aiMessage.textContent='Najpierw zbuduj workpack z wybraną referencją EN.';aiDownload.hidden=true}
});
polishSelect.addEventListener('change',()=>{aiDownload.hidden=true;aiMessage.textContent=''});
form.addEventListener('submit',hideAiHandoff);
document.querySelector('#rebuild').addEventListener('click',hideAiHandoff);
aiButton.addEventListener('click',async()=>{
  const jobId=aiJobId;
  if(!jobId||aiRunning)return;
  aiRunning=true;aiButton.disabled=true;polishSelect.disabled=true;aiDownload.hidden=true;
  aiButton.textContent='AI pracuje…';
  line('INFO','AI_SYNC','Wysyłanie referencji EN i istniejących napisów PL do skonfigurowanego API');
  aiRequestJobId=jobId;aiStartedAt=Date.now();updateAiElapsed();aiTimer=setInterval(updateAiElapsed,1000);
  try{
    const response=await fetch(`/api/tasks/${jobId}/ai-sync`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({polish_file:polishSelect.value,reference_source_id:aiPreparedReference})});
    const body=await response.json();
    stopAiElapsed();
    if(!response.ok)throw new Error(body.detail?.message||'Synchronizacja AI nie powiodła się');
    if(jobId===activeJobId){showAiResult(body,jobId);line('SUCCESS','AI_SYNC',`Zapisano SRT. Czas żądania: ${body.elapsed_seconds} s.`)}
  }catch(error){if(jobId===activeJobId){aiMessage.textContent=error.message;line('ERROR','AI_SYNC',error.message)}}
  finally{stopAiElapsed();aiRunning=false;polishSelect.disabled=false;aiButton.textContent='Przekaż do AI';aiButton.disabled=!polishSelect.options.length||referenceSelect.value!==aiPreparedReference}
});
