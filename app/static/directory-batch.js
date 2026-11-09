const batchLabels={pending:'Oczekuje',running:'Przetwarzanie',attention:'Wymaga wyboru referencji',ready:'Gotowe',failed:'Błąd',skipped:'Pominięto'};
const batchFieldLabels={container:'Kontener',width:'Szerokość',height:'Wysokość',video_codec:'Wideo',fps:'Klatkaż',referenceCodec:'Format referencji',referenceLanguage:'Język referencji',referenceType:'Typ referencji',referenceForced:'Forced'};
function batchValueText(key,value){
  if(value===null||value===undefined||value==='')return 'brak danych';
  if(key==='fps'){
    const decimal=typeof value==='object'?value.decimal:Number(value);
    const fraction=typeof value==='object'?value.fraction:null;
    if(typeof decimal==='number'&&Number.isFinite(decimal)&&decimal>0)return `${decimal.toLocaleString('pl-PL',{maximumFractionDigits:3})} kl./s${!Number.isInteger(decimal)&&fraction?` (${fraction})`:''}`;
    return fraction?`${fraction} kl./s`:'brak danych';
  }
  if(key==='container'){
    const formats=String(value).toLowerCase().split(',');
    if(formats.includes('matroska'))return 'Matroska (MKV)';
    if(formats.includes('mp4')||formats.includes('mov'))return 'MP4 / MOV';
    return String(value).toUpperCase();
  }
  if(key==='video_codec')return ({hevc:'HEVC (H.265)',h264:'H.264',mpeg4:'MPEG-4',av1:'AV1',vp9:'VP9'})[value]||String(value).toUpperCase();
  if(key==='referenceCodec')return ({subrip:'SRT',srt:'SRT',ass:'ASS',ssa:'SSA',webvtt:'WebVTT',hdmv_pgs_subtitle:'PGS',dvd_subtitle:'VobSub',mov_text:'MP4 Timed Text'})[value]||String(value).toUpperCase();
  if(key==='referenceLanguage')return ({eng:'angielski',en:'angielski',english:'angielski',pol:'polski',pl:'polski',fra:'francuski',fr:'francuski',deu:'niemiecki',de:'niemiecki',spa:'hiszpański',es:'hiszpański',ita:'włoski',it:'włoski',por:'portugalski',pt:'portugalski',jpn:'japoński',ja:'japoński',rus:'rosyjski',ru:'rosyjski',ukr:'ukraiński',uk:'ukraiński',ces:'czeski',cs:'czeski',nld:'niderlandzki',nl:'niderlandzki',swe:'szwedzki',sv:'szwedzki',dan:'duński',da:'duński',nor:'norweski',no:'norweski',fin:'fiński',fi:'fiński',kor:'koreański',ko:'koreański',zho:'chiński',zh:'chiński',ara:'arabski',ar:'arabski',tur:'turecki',tr:'turecki',ell:'grecki',el:'grecki',hun:'węgierski',hu:'węgierski',ron:'rumuński',ro:'rumuński',und:'nieokreślony'})[String(value).toLowerCase()]||String(value).toUpperCase();
  if(key==='referenceType')return ({text:'tekstowa',graphic:'graficzna'})[value]||String(value);
  if(typeof value==='boolean')return value?'tak':'nie';
  return typeof value==='object'?'brak danych':String(value);
}
function batchReferenceText(reference){
  if(!reference)return 'brak wskazanej referencji';
  const parts=[];
  if(reference.language)parts.push(batchValueText('referenceLanguage',reference.language));
  const type=reference.type?batchValueText('referenceType',reference.type):'';
  const codec=reference.codec?batchValueText('referenceCodec',reference.codec):'';
  if(type||codec)parts.push(type&&codec?`${type} (${codec})`:type||codec);
  if(typeof reference.forced==='boolean')parts.push(`forced: ${batchValueText('referenceForced',reference.forced)}`);
  return parts.join(' · ')||'brak danych referencji';
}
function batchCommonParameters(common){
  const rows=[];
  const add=(label,value)=>rows.push(`<dt>${nodeText(label)}</dt><dd>${nodeText(value)}</dd>`);
  if(common.container!=null)add('Kontener',batchValueText('container',common.container));
  if(common.width!=null&&common.height!=null)add('Rozdzielczość',`${common.width} × ${common.height}`);
  else for(const key of ['width','height'])if(common[key]!=null)add(batchFieldLabels[key],`${common[key]} px`);
  for(const key of ['video_codec','fps'])if(common[key]!=null)add(batchFieldLabels[key],batchValueText(key,common[key]));
  const reference={language:common.referenceLanguage,codec:common.referenceCodec,type:common.referenceType,forced:common.referenceForced};
  if(Object.values(reference).some(value=>value!=null))add('Referencja',batchReferenceText(reference));
  return rows.length?`<h3>Wspólne parametry</h3><dl>${rows.join('')}</dl>`:'';
}
function batchSubtitleInspection(inspection,total){
  if(!inspection)return '';
  if(!inspection.total)return '<h3>Napisy</h3><p>Brak zakończonych analiz napisów.</p>';
  return `<h3>Napisy</h3><p>Przeanalizowane pliki: <strong>${inspection.total}/${total}</strong> · wskazana referencja: <strong>${inspection.withReference}/${inspection.total}</strong> · napisy PL: <strong>${inspection.withPolish}/${inspection.total}</strong> · wymagają uwagi: <strong>${inspection.needsAttention}</strong></p>`+
    `<details${inspection.needsAttention?' open':''}><summary>Napisy w poszczególnych plikach</summary><div class="table-scroll"><table class="archive-table inspection-table"><thead><tr><th>Plik</th><th>Referencja</th><th>Napisy PL</th><th>Ocena techniczna</th></tr></thead><tbody>${inspection.items.map(item=>`<tr><td>${nodeText(item.name)}</td><td>${nodeText(batchReferenceText(item.reference))}</td><td>Zewnętrzne: ${item.polishFiles.length} · osadzone: ${item.embeddedPolishCount||0}${item.polishFiles.length?`<small>${nodeText(item.polishFiles.join(', '))}</small>`:''}${item.incompatiblePolishCount?`<small class="warning-box">Niezgodne czasowo: ${item.incompatiblePolishCount}</small>`:''}</td><td${item.attentionReasons.length?' class="warning-box"':''}>${nodeText(item.attentionReasons.length?item.attentionReasons.join(' · '):'Bez ostrzeżeń technicznych')}</td></tr>`).join('')}</tbody></table></div></details>`+
    '<p class="inspection-note">Ocena dotyczy metadanych i dopasowania plików. Nie potwierdza zgodności dialogów ani poprawnej synchronizacji.</p>';
}
function batchRow(item){
  const state=Object.hasOwn(batchLabels,item.state)?item.state:'pending';
  const incomplete=state==='ready'&&['WORKPACK_INCOMPLETE','NEEDS_OCR'].includes(item.resultStatus);
  const icons={pending:'clock',running:'play',attention:'pause',ready:'check',failed:'circle-exclamation',skipped:'minus'};
  const label=incomplete?'Niekompletne materiały':state==='ready'?'Zakończono':batchLabels[state];
  const detail=[label,state==='running'?`${item.progress==null?'AI':item.progress+'%'} · ${item.stage||''}`:'',item.error||''].filter(Boolean).join(' · ');
  const attribute=value=>nodeText(value).replaceAll('"','&quot;');
  return `<li data-state="${state}"${item.ai?' data-ai="true"':''}${state==='running'?' aria-current="step"':''}><strong class="batch-name" title="${attribute(item.name)}">${nodeText(item.name)}</strong><span class="batch-state is-${incomplete?'incomplete':state}" role="img" aria-label="${attribute(detail)}" title="${attribute(detail)}"><i class="fa-solid fa-${incomplete?'triangle-exclamation':icons[state]}" aria-hidden="true"></i>${state==='running'?` ${item.progress==null?'AI':nodeText(item.progress)+'%'}`:''}</span></li>`;
}
const renderedBatchJobs=new WeakMap();
function renderBatchList(panel,html){
  const previous=renderedBatchJobs.get(panel)===activeJobId?panel.querySelector('.batch-list')?.scrollTop||0:0;
  renderedBatchJobs.set(panel,activeJobId);
  panel.innerHTML=html;
  requestAnimationFrame(()=>{
    if(panel.hidden)return;
    const list=panel.querySelector('.batch-list');
    if(!list)return;
    list.scrollTop=previous;
    const current=list.querySelector('[aria-current="step"]')||list.querySelector('[data-state="attention"]');
    if(!current)return;
    const viewport=list.getBoundingClientRect(),row=current.getBoundingClientRect();
    if(row.top<viewport.top)list.scrollTop+=row.top-viewport.top;
    else if(row.bottom>viewport.bottom)list.scrollTop+=row.bottom-viewport.bottom;
  });
}
function renderDirectoryBatch(job){
  const report=job.report,batch=report.batch,items=batch.items||[],finished=items.filter(item=>['ready','failed','skipped'].includes(item.state)).length;
  activeDirectoryJob=job;activeJobId=job.jobId;activeMediaTitle=job.displayTitle;
  setDownload(job,report.workpack);
  const panel=document.querySelector('#batch-progress');panel.hidden=false;
  renderBatchList(panel,`<p class="batch-summary"><strong>${finished}/${items.length} zakończonych</strong>${batch.scanned?'':' · Skanowanie katalogu…'}</p><ol class="batch-list">${items.map(batchRow).join('')}</ol>`);
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
  const summary=report.summary||{};
  content.innerHTML=`<p>Katalog: ${nodeText(job.displayTitle)} · pliki: ${items.length} · pominięte dodatki: ${batch.ignoredFiles||0}</p>`+
    (report.workpack?`<p>ZIP: ${fileSize(report.workpack.sizeBytes)} · SHA-256: ${nodeText(report.workpack.sha256)}</p>`:'')+
    batchSubtitleInspection(summary.subtitleInspection,items.length)+batchCommonParameters(summary.common||{})+
    Object.entries(summary.differences||{}).map(([key,groups])=>`<details><summary>${nodeText(batchFieldLabels[key]||key)} — różnice (${groups.length} wariantów)</summary><ul>${groups.map(group=>`<li>${nodeText(batchValueText(key,group.value))}: ${nodeText(group.items.join(', '))}</li>`).join('')}</ul></details>`).join('')+
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
    if(!row)return {...item,state:'skipped',ai:true,error:'Poza kolejką AI',resultStatus:undefined};
    const status=row.status==='completed'?'ready':row.status==='failed'?'failed':
      row.status==='running'?(state.status==='running'?'running':'failed'):'pending';
    return {...item,state:status,ai:true,progress:null,stage:state.phase,error:row.error||'',resultStatus:undefined};
  });
  const finished=rows.filter(row=>['completed','failed'].includes(row.status)).length;
  const progress=rows.length?Math.round(finished/rows.length*100):0;
  const failed=rows.filter(row=>row.status==='failed').length;
  const panel=document.querySelector('#ai-batch-progress');panel.hidden=false;
  renderBatchList(panel,`<p class="batch-summary"><strong>AI: ${finished}/${rows.length} zakończonych · ${progress}%${failed?` · błędy: ${failed}`:''}</strong></p><div class="progress-track ai-progress-track" role="progressbar" aria-label="Postęp kolejki AI" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span style="width: ${progress}%"></span></div><p class="ai-batch-phase">${nodeText(state.phase||'AI')}</p><ol class="batch-list">${items.map(batchRow).join('')}</ol>`);
}
