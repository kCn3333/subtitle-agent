const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
async function setup(response,state={status:'idle'},directory=null){
  const elements=new Map();
  function element(tag){return {tagName:tag?.toUpperCase(),dataset:{},get outerHTML(){return String(this.textContent??'')},querySelectorAll(selector){return this.options.flatMap(child=>[...(child.tagName===selector.toUpperCase()?[child]:[]),...child.querySelectorAll(selector)])},setAttribute(name,value){this[name]=value},hidden:false,disabled:false,options:[],value:'',handlers:{},
    contains(node){return this.options.includes(node)},
    remove(){for(const node of elements.values())node.options=node.options.filter(item=>item!==this)},
    addEventListener(name,handler){this.handlers[name]=handler},
    replaceChildren(){this.options=[];this.value=''},
    append(...options){this.options.push(...options);if(this.options.length===options.length)this.value=options[0]?.value}}}
  const get=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)};
  get('#reference-source').value='embedded:4';
  const context={output:get('#console'),now:0,document:{querySelector:get,createElement:element},activeJobId:'job',form:get('#job-form'),
    referenceSelect:get('#reference-source'),sourceId:item=>`embedded:${item.streamIndex}`,logs:[],sent:[],progress:[],renderDirectoryAiProgress(state){context.progress.push(state)},line(...args){context.logs.push(args)},
    fetch:(url,options)=>{if(options?.method==='POST'){context.sent.push({url,body:JSON.parse(options.body)});return Promise.resolve(response)}return Promise.resolve({ok:true,json:async()=>url.endsWith('/ai-inputs')?directory?.inputs:url.endsWith('/ai-status')?state:{result:null}})}};
  if(directory?.fromCache)context.activeDirectoryJob=directory.job;
  context.Date={now:()=>context.now,parse:Date.parse};
  context.setInterval=(callback,ms)=>{if(ms===1000)context.timerCallback=callback;else context.pollCallback=callback;return ms};
  context.clearInterval=id=>{if(id===1000)context.timerCallback=null};
  const script=readFileSync('app/static/ai-sync.js','utf8');
  runInNewContext(readFileSync('app/static/ai-metrics.js','utf8'),context);
  runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  runInNewContext(readFileSync('app/static/app.js','utf8').match(/^function referenceNeedsConfirmation.*$/m)[0],context);
  runInNewContext(script,context);
  if(!directory?.fromCache)await context.renderAiSync(directory?.job||{jobId:'job',status:'WORKPACK_READY',displayTitle:'Come and See (1985)',report:{pipeline:'PREPARE_SYNC',
    selectedEnglish:{streamIndex:4},polishCandidates:[{archiveName:'polish/a.srt',originalName:'A'},{archiveName:'polish/b.srt'}]}});
  if(directory?.fromCache)await new Promise(resolve=>setImmediate(resolve));
  await context.refreshAiStatus('job');
  return {context,get};
}
test('changed EN requires rebuild and disables submission',async()=>{
  const {get}=await setup();
  assert.equal(get('#ai-sync-panel').hidden,false);
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.equal(get('#ai-sync-button').hidden,false);
  get('#reference-source').value='embedded:13';get('#reference-source').handlers.change();
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.match(get('#ai-sync-status').textContent,/Najpierw zbuduj/);
});
test('successful response shows measured time and tokens',async()=>{
  const {get,context}=await setup({ok:true,json:async()=>({cue_count:2,elapsed_seconds:1.25,filename:'Film.AI-Synced-v001.pl.srt',
    inputs:[{name:'reference/en.srt'},{name:'polish/a.srt'}],usage:{total_tokens:42}})});
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-download').hidden,false);
  assert.doesNotMatch(get('#ai-sync-status').textContent,/Tokeny|Koszt|1.25 s/);
  assert.match(context.logs.at(-1)[2],/1.25 s/);
  assert.equal(get('#ai-download').download,'Film.AI-Synced-v001.pl.srt');
  assert.match(context.logs[0][2],/Synchronizacja przez AI · Film: Come and See \(1985\)/);
  assert.match(context.logs.at(-1)[2],/42/);
  assert.equal(context.logs.at(-1)[1],'AI_SYNC_SUMMARY');
  assert.match(context.logs.at(-1)[2],/Wygenerowano gotowy plik SRT \(2 kwestii\)/);
  get('#ai-polish').handlers.change();
  assert.equal(get('#ai-download').hidden,true);
});
test('invalid response displays error without download',async()=>{
  const {get}=await setup({ok:false,json:async()=>({detail:{message:'Niepełna odpowiedź'}})});
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-download').hidden,true);
  assert.equal(get('#ai-sync-status').textContent,'Niepełna odpowiedź');
  assert.equal(get('#ai-sync-button').disabled,false);
});
test('result from different Polish file is not displayed',async()=>{
  const {get,context}=await setup();
  context.showAiResult({cue_count:1,elapsed_seconds:1,inputs:[{}, {name:'polish/b.srt'}]},'job');
  assert.equal(get('#ai-download').hidden,true);
});

test('only clicking handoff starts console timer, which stops on completion',async()=>{
  let finish;
  const pending=new Promise(resolve=>finish=resolve);
  const {get,context}=await setup(pending);
  assert.equal(context.timerCallback,undefined);
  assert.equal(get('#console').options.length,0);
  const operation=get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-sync-button-label').textContent,'AI pracuje…');
  context.now=65000;context.timerCallback();
  assert.match(get('#console').options[0].options[1].textContent,/1 min 05 s/);
  assert.match(get('#ai-sync-status').textContent,/65 s/);
  const previous=get('#ai-sync-status').textContent;
  context.now=66000;context.timerCallback();
  assert.match(previous,/żądanie\.\.\./);
  assert.match(get('#ai-sync-status').textContent,/żądanie\. Czas/);
  assert.doesNotMatch(get('#console').options[0].options[1].textContent,/procentowego postępu/);
  get('#console').replaceChildren();context.updateAiElapsed();
  assert.equal(get('#console').options.length,1);
  context.activeJobId='other';context.timerCallback();
  assert.equal(get('#console').options.length,0);
  context.activeJobId='job';context.timerCallback();
  assert.equal(get('#console').options.length,1);
  finish({ok:true,json:async()=>({cue_count:1,elapsed_seconds:65,inputs:[{}, {name:'polish/a.srt'}]})});
  await operation;
  assert.equal(context.timerCallback,null);
  assert.equal(get('#console').options.length,0);
  assert.equal(get('#ai-sync-button-label').textContent,'Przekaż do AI');
});

test('translation handoff needs no PL and selects translation endpoint',async()=>{
  const {get,context}=await setup({ok:true,json:async()=>({cue_count:1,elapsed_seconds:2,inputs:[{name:'en.srt'}],
    usage:{total_tokens:30,cost:0.00125,cost_currency:'USD'},total_cost:0.00125})});
  let request;
  context.fetch=(url,options)=>{if(!options?.method)return Promise.resolve({ok:true,json:async()=>({status:'idle'})});request={url,options};return Promise.resolve({ok:true,json:async()=>({cue_count:1,elapsed_seconds:2,inputs:[{name:'en.srt'}],
    usage:{total_tokens:30,cost:0.00125,cost_currency:'USD'},total_cost:0.00125})})};
  context.renderAiSync({jobId:'job',status:'WORKPACK_READY',displayTitle:'Come and See (1985)',report:{pipeline:'PREPARE_TRANSLATION',selectedEnglish:{streamIndex:4}}});
  assert.equal(get('#ai-polish-field').hidden,true);
  assert.equal(get('#ai-panel-title').textContent,'Tłumaczenie przez AI');
  await context.refreshAiStatus();
  assert.equal(get('#ai-sync-button').disabled,false);
  await get('#ai-sync-button').handlers.click();
  assert.equal(request.url,'/api/tasks/job/ai-translate');
  assert.deepEqual(JSON.parse(request.options.body),{reference_source_id:'embedded:4'});
  assert.equal(get('#ai-download').href,'/api/tasks/job/ai-translate/download');
  assert.doesNotMatch(get('#ai-sync-status').textContent,/Koszt|Tokeny/);
  assert.match(context.logs.at(-1)[2],/Koszt całej operacji: 0,00125 USD/);
  assert.equal((context.logs.at(-1)[2].match(/0,00125 USD/g)||[]).length,1);
  assert.doesNotMatch(context.logs.at(-1)[2],/Koszt żądania/);
  assert.match(context.logs.at(-1)[2],/gotowy plik SRT/);
});
test('zero cost is displayed and absent cost is not treated as free',async()=>{
  const {context}=await setup();
  assert.match(context.formatAiCostSummary({usage:{cost:0,cost_currency:'USD'}}),/0 USD/);
  assert.match(context.formatAiCostSummary({usage:{total_tokens:3}}),/API nie podało kosztu/);
  assert.match(context.aiCostText(1e-21,{cost_currency:'USD'}),/1e-21 USD/);
});
test('paid invalid response retains costs in the progress console message',async()=>{
  const {get,context}=await setup({ok:false,json:async()=>({detail:{message:'Niepełne tłumaczenie',elapsed_seconds:3,
    usage:{total_tokens:20,cost:0.0001,cost_currency:'USD'}}})});
  await get('#ai-sync-button').handlers.click();
  assert.match(get('#ai-sync-status').textContent,/Niepełne tłumaczenie/);
  assert.doesNotMatch(get('#ai-sync-status').textContent,/Koszt|Tokeny/);
  assert.match(context.logs.at(-1)[2],/Koszt całej operacji: 0,0001 USD/);
  assert.equal((context.logs.at(-1)[2].match(/0,0001 USD/g)||[]).length,1);
  assert.doesNotMatch(context.logs.at(-1)[2],/gotowy plik SRT/);
  assert.equal(get('#ai-download').hidden,true);
});
test('returning to page restores running indicator, elapsed time and disabled controls',async()=>{
  const state={status:'running',operation_id:'op1',mode:'sync',started_at:'1970-01-01T00:00:00Z',
    title:'Come and See (1985)',polish_file:'polish/b.srt',phase:'Oczekiwanie na odpowiedź modelu'};
  for(let i=0;i<2;i++){
    const {get,context}=await setup(undefined,state);
    assert.equal(get('#ai-sync-button').disabled,true);
    assert.equal(get('#ai-polish').disabled,true);
    assert.equal(get('#rebuild').disabled,true);
    assert.equal(get('#ai-polish').value,'polish/b.srt');
    assert.equal(get('#ai-sync-button-label').textContent,'AI pracuje…');
    assert.match(get('#console').options[0].options[0].className,/ai-spinner/);
    context.now=65000;context.timerCallback();
    assert.match(get('#console').options[0].options[1].textContent,/1 min 05 s/);
    await get('#ai-sync-button').handlers.click();assert.equal(context.logs.length,0);
    get('#console').replaceChildren();context.updateAiElapsed();assert.equal(get('#console').options.length,1);
    if(i===1){
      state.status='completed';state.result={cue_count:2,elapsed_seconds:65,usage:{total_tokens:42},inputs:[{}, {name:'polish/b.srt'}]};
      await context.refreshAiStatus();
      assert.equal(get('#ai-sync-button').disabled,false);
      assert.equal(get('#console').options.length,0);
      assert.equal(get('#ai-download').hidden,false);
      assert.equal(context.logs.length,1);assert.match(context.logs[0][2],/42/);
      await context.refreshAiStatus();assert.equal(context.logs.length,1);
    }
  }
});
test('409 restores active operation without a false error or cost summary',async()=>{
  const {get,context}=await setup();
  context.fetch=async(url,options)=>options?.method==='POST'?{ok:false,status:409,json:async()=>({detail:{message:'Operacja AI tego zadania już trwa'}})}:
    {ok:true,json:async()=>({status:'running',operation_id:'existing',mode:'sync',started_at:'1970-01-01T00:00:00Z'})};
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.equal(get('#console').options.length,1);
  assert.equal(context.logs.filter(entry=>entry[0]==='ERROR'||entry[1].endsWith('_SUMMARY')).length,0);
});
test('failed polling keeps handoff disabled until server state is known',async()=>{
  const {get,context}=await setup();
  context.fetch=async()=>{throw new Error('network lost')};
  await context.refreshAiStatus();
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.match(get('#ai-sync-status').textContent,/Ponawiam/);
  context.fetch=async()=>({ok:true,json:async()=>({status:'idle'})});
  await context.refreshAiStatus();assert.equal(get('#ai-sync-button').disabled,false);
});
test('network failure during handoff restores server activity without false completion summary',async()=>{
  const {get,context}=await setup();
  context.fetch=async(url,options)=>{
    if(options?.method==='POST')throw new Error('Response connection lost');
    return {ok:true,json:async()=>({status:'running',operation_id:'continuing',mode:'sync',started_at:'1970-01-01T00:00:00Z'})};
  };
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.equal(get('#console').options.length,1);
  assert.equal(context.logs.filter(entry=>entry[1].endsWith('_SUMMARY')).length,0);
});
test('failure after return shows metrics once and stops animation',async()=>{
  const state={status:'running',operation_id:'op-fail',mode:'sync',started_at:'1970-01-01T00:00:00Z'};
  const {get,context}=await setup(undefined,state);
  state.status='failed';state.error={message:'Model returned invalid JSON',elapsed_seconds:2,usage:{total_tokens:42,cost:0.01,cost_currency:'USD'}};
  await context.refreshAiStatus();
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.equal(get('#console').options.length,0);
  assert.equal(get('#ai-download').hidden,true);
  assert.match(get('#ai-sync-status').textContent,/invalid JSON/);
  assert.match(context.logs.at(-1)[2],/42/);
  assert.match(context.logs.at(-1)[2],/0,01 USD/);
  const count=context.logs.length;await context.refreshAiStatus();assert.equal(context.logs.length,count);
});

for(const pipeline of ['PREPARE_SYNC','PREPARE_TRANSLATION']){
  for(const external of [false,true]){
    test(`${pipeline} waits for ${external?'external':'ambiguous'} reference confirmation and unlocks after rebuild`,async()=>{
      const {get,context}=await setup();
      let posts=0;
      context.fetch=async(url,options)=>{if(options?.method==='POST')posts++;return {ok:true,json:async()=>({status:'idle'})}};
      const report={pipeline,selectedReference:external?null:{streamIndex:4},
        polishCandidates:[{archiveName:'polish/a.srt'}],
        externalReferenceConfirmationRequired:external,workpack:{referenceAmbiguous:!external}};
      context.renderAiSync({jobId:'job',status:external?'WORKPACK_INCOMPLETE':'WORKPACK_READY',report});
      await context.refreshAiStatus();
      assert.equal(get('#ai-sync-panel').hidden,false);
      assert.equal(get('#ai-sync-button').hidden,false);
      assert.equal(get('#ai-sync-button').disabled,true);
      assert.equal(get('#rebuild').disabled,false);
      assert.match(get('#ai-sync-status').textContent,/Najpierw zatwierdź/);
      await get('#ai-sync-button').handlers.click();
      assert.equal(posts,0);
      await context.refreshAiStatus();
      assert.equal(get('#ai-sync-button').disabled,true);
      context.renderAiSync({jobId:'job',status:'WORKPACK_READY',report:{...report,
        selectedReference:{streamIndex:4},externalReferenceConfirmationRequired:false,workpack:{referenceAmbiguous:false}}});
      await context.refreshAiStatus();
      assert.equal(get('#ai-sync-button').disabled,false);
    });
  }
}

test('queued AI shows waiting state and transitions to running and completion',async()=>{
  const state={status:'queued',operation_id:'queued-op',mode:'sync',queue_position:2,
    queued_at:'1970-01-01T00:00:00Z',phase:'Oczekiwanie w kolejce AI'};
  const {get,context}=await setup(undefined,state);
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.equal(get('#rebuild').disabled,true);
  assert.equal(get('#ai-sync-button-label').textContent,'Oczekuje w kolejce…');
  assert.match(get('#ai-sync-status').textContent,/pozycja 2/);
  state.status='running';state.started_at='1970-01-01T00:00:05Z';state.phase='Oczekiwanie na odpowiedź modelu';
  await context.refreshAiStatus();
  assert.equal(get('#ai-sync-button-label').textContent,'AI pracuje…');
  state.status='completed';state.result={cue_count:2,elapsed_seconds:4,inputs:[{}, {name:'polish/a.srt'}]};
  await context.refreshAiStatus();
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.equal(get('#ai-download').hidden,false);
  assert.equal(context.logs.filter(entry=>entry[1]==='AI_SYNC_SUMMARY').length,1);
});

test('202 acceptance waits for status instead of displaying an unfinished result',async()=>{
  const state={status:'idle'};
  const {get,context}=await setup(undefined,state);
  context.fetch=async(url,options)=>{
    if(options?.method==='POST'){
      Object.assign(state,{status:'queued',operation_id:'new-op',mode:'sync',queue_position:1,
        queued_at:'1970-01-01T00:00:00Z',phase:'Oczekiwanie w kolejce AI'});
      return {ok:true,status:202,json:async()=>({...state})};
    }
    return {ok:true,json:async()=>({...state})};
  };
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-download').hidden,true);
  assert.equal(get('#ai-sync-button-label').textContent,'Oczekuje w kolejce…');
  assert.equal(context.logs.filter(entry=>entry[1].endsWith('_SUMMARY')).length,0);
});

function directoryFixture(mode='PREPARE_SYNC',available=true){
  return {job:{jobId:'job',status:'WORKPACK_READY',displayTitle:'Season 01',report:{mode,batch:{items:[]},workpack:{filename:'season.zip'}}},
    inputs:{mode,items:[{job_id:'episode-1',name:'S01E01.mkv',available,reference_source_id:'embedded:2',
      polish_candidates:mode==='PREPARE_SYNC'?[{archiveName:'polish/a.srt',originalName:'A'},{archiveName:'polish/b.srt',originalName:'B'}]:[]}]}};
}
test('prepared directory exposes the same AI action and submits each selected episode input',async()=>{
  const directory=directoryFixture(),state={status:'idle'};
  const queued={job_id:'job',operation_id:'directory-ai',batch:true,status:'queued',mode:'sync',title:'Season 01',
    items:[{job_id:'episode-1',name:'S01E01.mkv',polish_file:'polish/b.srt',status:'pending'}]};
  const {get,context}=await setup({ok:true,status:202,json:async()=>queued},state,directory);
  assert.equal(get('#ai-sync-panel').hidden,false);
  assert.equal(get('#ai-sync-button').hidden,false);
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.equal(get('#ai-polish-field').hidden,true);
  const select=get('#ai-directory-inputs').querySelectorAll('select')[0];
  select.value='polish/b.srt';select.handlers.change();
  Object.assign(state,queued);
  await get('#ai-sync-button').handlers.click();
  assert.equal(context.sent[0].url,'/api/tasks/job/ai-sync');
  assert.deepEqual(context.sent[0].body,{items:[{job_id:'episode-1',reference_source_id:'embedded:2',polish_file:'polish/b.srt'}]});
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.equal(select.disabled,true);
  assert.equal(context.progress.at(-1).batch,true);
});
test('directory result restores choices, downloads ZIP and hides stale results after selection changes',async()=>{
  const directory=directoryFixture();
  const result={batch:true,mode:'sync',file_count:1,failed_count:0,cue_count:1,filename:'Season.AI-Synced-v001.zip',
    outputs:[{job_id:'episode-1',polish_file:'polish/b.srt'}]};
  const state={job_id:'job',batch:true,status:'completed',operation_id:'completed',mode:'sync',result,
    items:[{job_id:'episode-1',name:'S01E01.mkv',polish_file:'polish/b.srt',status:'completed'}]};
  const {get,context}=await setup(null,state,directory);
  const select=get('#ai-directory-inputs').querySelectorAll('select')[0];
  assert.equal(select.value,'polish/b.srt');
  assert.equal(get('#ai-download').hidden,false);
  assert.equal(get('#ai-download-label').textContent,'Pobierz wyniki ZIP');
  assert.equal(get('#ai-download').download,result.filename);
  assert.match(context.logs.at(-1)[2],/Wyniki AI: 1 plików SRT/);
  assert.doesNotMatch(context.logs.at(-1)[2],/gotowy plik SRT/);
  select.value='polish/a.srt';select.handlers.change();
  context.applyAiState(state,'job');
  assert.equal(select.value,'polish/a.srt');
  assert.equal(get('#ai-download').hidden,true);
});
test('directory translation needs no Polish selection and all-skipped directories explain disabled AI',async()=>{
  const translated=await setup(null,{status:'idle'},directoryFixture('PREPARE_TRANSLATION'));
  assert.equal(translated.get('#ai-sync-button').disabled,false);
  assert.equal(translated.get('#ai-directory-inputs').querySelectorAll('select').length,0);
  const skipped=await setup(null,{status:'idle'},directoryFixture('PREPARE_SYNC',false));
  assert.equal(skipped.get('#ai-sync-panel').hidden,false);
  assert.equal(skipped.get('#ai-sync-button').disabled,true);
  assert.match(skipped.get('#ai-sync-status').textContent,/Brak gotowych materiałów/);
});
test('partial directory AI failure keeps successful results downloadable',async()=>{
  const directory=directoryFixture();
  const state={job_id:'job',batch:true,status:'failed',operation_id:'partial',mode:'sync',error:{message:'Jeden odcinek nie powiódł się'},
    items:[{job_id:'episode-1',name:'S01E01.mkv',polish_file:'polish/a.srt',status:'completed'}],
    result:{batch:true,mode:'sync',file_count:1,failed_count:1,cue_count:1,filename:'partial.zip',outputs:[{job_id:'episode-1',polish_file:'polish/a.srt'}]}};
  const {get}=await setup(null,state,directory);
  assert.equal(get('#ai-download').hidden,false);
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.match(get('#ai-sync-status').textContent,/Jeden odcinek/);
});

test('loading AI controls after a restored directory still exposes the handoff',async()=>{
  const directory={...directoryFixture(),fromCache:true};
  const {get}=await setup(null,{status:'idle'},directory);
  assert.equal(get('#ai-sync-panel').hidden,false);
  assert.equal(get('#ai-sync-button').hidden,false);
  assert.equal(get('#ai-sync-button').disabled,false);
});
