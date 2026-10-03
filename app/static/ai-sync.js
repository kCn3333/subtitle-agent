const aiPanel=document.querySelector('#ai-sync-panel'),polishSelect=document.querySelector('#ai-polish');
const aiButton=document.querySelector('#ai-sync-button'),aiMessage=document.querySelector('#ai-sync-status'),aiDownload=document.querySelector('#ai-download');
let aiPreparedReference=null,aiRunning=false,aiJobId=null;
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
  if(aiPanel.hidden)return;
  aiJobId=job.jobId;aiPreparedReference=sourceId(report.selectedEnglish);
  polishSelect.replaceChildren();
  for(const item of report.polishCandidates||[]){
    if(!item.archiveName?.toLowerCase().endsWith('.srt'))continue;
    const option=document.createElement('option');option.value=item.archiveName;option.textContent=item.originalName||item.archiveName;polishSelect.append(option);
  }
  aiButton.disabled=aiRunning||!polishSelect.options.length||referenceSelect.value!==aiPreparedReference;
  aiMessage.textContent='';aiDownload.hidden=true;
  fetch(`/api/tasks/${job.jobId}/ai-sync`).then(response=>response.ok?response.json():null)
    .then(body=>{if(!aiRunning)showAiResult(body?.result,job.jobId)}).catch(()=>{});
}
referenceSelect.addEventListener('change',()=>{
  aiButton.disabled=aiRunning||!polishSelect.options.length||referenceSelect.value!==aiPreparedReference;
  if(referenceSelect.value!==aiPreparedReference){aiMessage.textContent='Najpierw zbuduj workpack z wybraną referencją EN.';aiDownload.hidden=true}
});
polishSelect.addEventListener('change',()=>{aiDownload.hidden=true;aiMessage.textContent=''});
form.addEventListener('submit',()=>{aiPanel.hidden=true});
document.querySelector('#rebuild').addEventListener('click',()=>{aiPanel.hidden=true});
aiButton.addEventListener('click',async()=>{
  const jobId=aiJobId;
  if(!jobId||aiRunning)return;
  aiRunning=true;aiButton.disabled=true;polishSelect.disabled=true;aiDownload.hidden=true;
  aiMessage.textContent='Oczekiwanie na odpowiedź modelu…';line('INFO','AI_SYNC','Wysyłanie referencji EN i istniejących napisów PL do skonfigurowanego API');
  try{
    const response=await fetch(`/api/tasks/${jobId}/ai-sync`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({polish_file:polishSelect.value,reference_source_id:aiPreparedReference})});
    const body=await response.json();
    if(!response.ok)throw new Error(body.detail?.message||'Synchronizacja AI nie powiodła się');
    if(jobId===activeJobId){showAiResult(body,jobId);line('SUCCESS','AI_SYNC',`Zapisano SRT. Czas żądania: ${body.elapsed_seconds} s.`)}
  }catch(error){if(jobId===activeJobId){aiMessage.textContent=error.message;line('ERROR','AI_SYNC',error.message)}}
  finally{aiRunning=false;polishSelect.disabled=false;aiButton.disabled=!polishSelect.options.length||referenceSelect.value!==aiPreparedReference}
});
