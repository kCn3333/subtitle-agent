const batchLabels={pending:'Oczekuje',running:'Przetwarzanie',attention:'Wymaga wyboru referencji',ready:'Gotowe',failed:'Błąd',skipped:'Pominięto'};
function batchRow(item){
  const state=Object.hasOwn(batchLabels,item.state)?item.state:'pending';
  const incomplete=state==='ready'&&['WORKPACK_INCOMPLETE','NEEDS_OCR'].includes(item.resultStatus);
  const icons={pending:'clock',running:'play',attention:'pause',ready:'check',failed:'circle-exclamation',skipped:'minus'};
  const label=incomplete?'Niekompletne materiały':state==='ready'?'Zakończono':batchLabels[state];
  const detail=[label,state==='running'?`${item.progress==null?'AI':item.progress+'%'} · ${item.stage||''}`:'',item.error||''].filter(Boolean).join(' · ');
  const attribute=value=>nodeText(value).replaceAll('"','&quot;');
  return `<li data-state="${state}"${item.ai?' data-ai="true"':''}${state==='running'?' aria-current="step"':''}><strong class="batch-name" title="${attribute(item.name)}">${nodeText(item.name)}</strong><span class="batch-state is-${incomplete?'incomplete':state}" role="img" aria-label="${attribute(detail)}" title="${attribute(detail)}"><i class="fa-solid fa-${incomplete?'triangle-exclamation':icons[state]}" aria-hidden="true"></i>${state==='running'?` ${item.progress==null?'AI':nodeText(item.progress)+'%'}`:''}</span></li>`;
}
function renderDirectoryBatch(job){
  const report=job.report,batch=report.batch,items=batch.items||[],finished=items.filter(item=>['ready','failed','skipped'].includes(item.state)).length;
  activeDirectoryJob=job;activeJobId=job.jobId;activeMediaTitle=job.displayTitle;
  setDownload(job,report.workpack);
  const panel=document.querySelector('#batch-progress');panel.hidden=false;
  panel.innerHTML=`<p class="batch-summary"><strong>${finished}/${items.length} zakończonych</strong>${batch.scanned?'':' · Skanowanie katalogu…'}</p><ol class="batch-list">${items.map(batchRow).join('')}</ol>`;
  const attention=report.attention||{};
  referenceControls(attention);
  document.querySelector('#batch-skip').hidden=!['BATCH_PAUSED','INTERRUPTED'].includes(job.status)||batch.current==null;
  document.querySelector('#batch-resume').hidden=!['INTERRUPTED','FAILED'].includes(job.status);
  document.querySelector('#batch-retry').hidden=!['WORKPACK_INCOMPLETE','FAILED'].includes(job.status)||!items.some(item=>item.state==='failed');
  activityEl.textContent=job.status==='BATCH_PAUSED'?'Kolejka wstrzymana — wybierz referencję lub pomiń bieżący plik.':
    batch.current!=null?`${items[batch.current]?.name} · ${items[batch.current]?.stage||'Oczekiwanie'}`:
    `${finished}/${items.length} zakończonych`;
  if(job.status==='BATCH_PAUSED'&&!attention.referenceRanking?.length){
    currentStatus.hidden=false;activityEl.textContent='Brak referencji. Pomiń bieżący plik, aby kontynuować.';
  }
  results.hidden=false;
  function valueText(value){return value&&typeof value==='object'?JSON.stringify(value):String(value??'—')}
  const summary=report.summary||{},labels={container:'Kontener',width:'Szerokość',height:'Wysokość',video_codec:'Kodek wideo',fps:'FPS',referenceCodec:'Kodek referencji',referenceLanguage:'Język referencji',referenceType:'Typ referencji',referenceForced:'Flaga forced referencji'};
  content.innerHTML=`<p>Katalog: ${nodeText(job.displayTitle)} · pliki: ${items.length} · pominięte dodatki: ${batch.ignoredFiles||0}</p>`+
    (report.workpack?`<p>ZIP: ${fileSize(report.workpack.sizeBytes)} · SHA-256: ${nodeText(report.workpack.sha256)}</p>`:'')+
    `<dl>${Object.entries(summary.common||{}).map(([key,value])=>`<dt>${nodeText(labels[key]||key)}</dt><dd>${nodeText(valueText(value))}</dd>`).join('')}</dl>`+
    Object.entries(summary.differences||{}).map(([key,groups])=>`<details><summary>${nodeText(labels[key]||key)} — różnice (${groups.length} wariantów)</summary><ul>${groups.map(group=>`<li>${nodeText(valueText(group.value))}: ${nodeText(group.items.join(', '))}</li>`).join('')}</ul></details>`).join('')+
    `<div class="warning-box">${(report.warnings||[]).map(warning=>`<p>${nodeText(warning)}</p>`).join('')}${(summary.warnings||[]).map(item=>`<details><summary>${nodeText(item.message)} (${item.items.length})</summary><p>${nodeText(item.items.join(', '))}</p></details>`).join('')}</div>`;
  if(job.errorMessage)activityEl.textContent=job.errorMessage;
  if(typeof renderAiSync==='function')renderAiSync(job);else if(typeof hideAiHandoff==='function')hideAiHandoff();
}
async function continueDirectoryBatch(action){
  if(!activeDirectoryJob)return;
  const jobId=activeDirectoryJob.jobId;
  const controls=['#rebuild','#batch-skip','#batch-resume','#batch-retry'].map(selector=>document.querySelector(selector));
  controls.forEach(button=>button.disabled=true);
  try{
    const response=await fetch(`/api/tasks/${jobId}/batch`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action,referenceSourceId:action==='reference'?referenceSelect.value:null})});
    const body=await response.json();
    if(!response.ok)throw new Error(body.detail?.message||'Nie udało się wznowić katalogu');
    await load(jobId);connect(jobId,body.afterSequence);
  }catch(error){line('ERROR','BATCH',error.message)}
  finally{controls.forEach(button=>button.disabled=false)}
}
document.querySelector('#batch-skip').addEventListener('click',()=>continueDirectoryBatch('skip'));
document.querySelector('#batch-resume').addEventListener('click',()=>continueDirectoryBatch('resume'));
document.querySelector('#batch-retry').addEventListener('click',()=>continueDirectoryBatch('retry'));
let batchPollBusy=false;
setInterval(async()=>{
  const job=activeDirectoryJob;
  if(batchPollBusy||!job||job.jobId!==activeJobId||['WORKPACK_READY','WORKPACK_INCOMPLETE','INSPECTION_READY','BATCH_PAUSED','FAILED','INTERRUPTED'].includes(job.status))return;
  batchPollBusy=true;
  try{await load(job.jobId)}catch{if(activeDirectoryJob?.jobId===job.jobId)activityEl.textContent='Nie udało się odczytać stanu katalogu. Ponawiam…';}finally{batchPollBusy=false}
},2000);

function renderDirectoryAiProgress(state){
  if(!activeDirectoryJob||state.job_id!==activeDirectoryJob.jobId)return;
  const rows=state.items||[],byId=new Map(rows.map(row=>[row.job_id,row]));
  const items=activeDirectoryJob.report.batch.items.map(item=>{
    const row=byId.get(item.jobId);
    if(!row)return item;
    const status=row.status==='completed'?'ready':row.status==='failed'?'failed':
      row.status==='running'?(state.status==='running'?'running':'failed'):'pending';
    return {...item,state:status,ai:true,progress:null,stage:state.phase,error:row.error||'',resultStatus:undefined};
  });
  const finished=rows.filter(row=>['completed','failed'].includes(row.status)).length;
  document.querySelector('#batch-progress').innerHTML=`<p class="batch-summary"><strong>AI: ${finished}/${rows.length} zakończonych</strong></p><ol class="batch-list">${items.map(batchRow).join('')}</ol>`;
  activityEl.textContent=state.phase||'AI';
}
