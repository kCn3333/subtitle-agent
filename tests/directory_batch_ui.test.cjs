const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(job){
  const elements=new Map(),requests=[],connections=[];
  function element(){return {hidden:false,disabled:false,options:[],handlers:{},setAttribute(name,value){this[name]=value},
    removeAttribute(name){delete this[name]},addEventListener(name,handler){this.handlers[name]=handler},
    replaceChildren(){this.options=[]},append(...items){this.options.push(...items)},
    get outerHTML(){return String(this.textContent??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}}}
  const get=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)};
  const context={document:{querySelector:get,createElement:element},job,requests,connections,
    fetch:async(url,options)=>{requests.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({afterSequence:42})}},
    setInterval(callback){context.poll=callback},load:async()=>{},connect:(...args)=>connections.push(args),line(){},
    hideAiHandoff(){get('#ai-sync-panel').hidden=true;get('#ai-sync-button').hidden=true}};
  runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  runInNewContext(readFileSync('app/static/app.js','utf8').split('async function load(')[0],context);
  runInNewContext(readFileSync('app/static/directory-batch.js','utf8'),context);
  runInNewContext('render(job)',context);
  return {get,context};
}
function job(status='BATCH_PROCESSING'){
  return {jobId:'batch',displayTitle:'Example Season',status,report:{jobType:'DIRECTORY_WORKPACK',batch:{scanned:true,current:1,
    items:[{name:'S01E01.mkv',state:'ready',progress:100},{name:'S01E02.mkv',state:status==='BATCH_PAUSED'?'attention':'running',progress:35,stage:'SELECTING_REFERENCE'}]}}};
}
test('batch status lists current episode, keeps aggregate download disabled and hides single-file AI',()=>{
  const {get}=setup(job());
  assert.match(get('#batch-progress').innerHTML,/1\/2 zakończonych/);
  assert.match(get('#batch-progress').innerHTML,/aria-current="step".*S01E02/);
  assert.equal(get('#download')['aria-disabled'],'true');
  assert.equal(get('#ai-sync-panel').hidden,true);
  assert.match(get('#current-activity').textContent,/S01E02/);
});
test('paused reference choice resumes the parent batch at its event cursor',async()=>{
  const paused=job('BATCH_PAUSED');
  paused.report.attention={selectedReference:{sourceType:'embedded',streamIndex:1},
    referenceRanking:[{sourceType:'embedded',streamIndex:1},{sourceType:'embedded',streamIndex:2}],workpack:{referenceAmbiguous:true}};
  const {get,context}=setup(paused);
  assert.equal(get('#reference-choice').hidden,false);
  assert.equal(get('#rebuild').textContent,'Zatwierdź wybór');
  assert.equal(get('#batch-skip').hidden,false);
  get('#reference-source').value='embedded:2';
  await context.continueDirectoryBatch('reference');
  assert.equal(context.requests[0][0],'/api/tasks/batch/batch');
  assert.deepEqual(context.requests[0][1],{action:'reference',referenceSourceId:'embedded:2'});
  assert.deepEqual(context.connections[0],['batch',42]);
  assert.equal(get('#rebuild').disabled,false);
  await context.poll();
  assert.equal(context.requests.length,1);
});
test('finished batch exposes only its combined ZIP and switching to a file hides batch controls',()=>{
  const completed=job('WORKPACK_READY');
  completed.report.batch.current=null;
  completed.report.workpack={filename:'season.zip',sha256:'abc',sizeBytes:123,referenceAmbiguous:false};
  const {get,context}=setup(completed);
  assert.equal(get('#download').href,'/api/tasks/batch/download');
  assert.equal(get('#download').download,'season.zip');
  context.job={jobId:'file',report:{jobType:'PREPARE_WORKPACK'}};
  runInNewContext('render(job)',context);
  assert.equal(get('#batch-progress').hidden,true);
  assert.equal(get('#batch-skip').hidden,true);
  assert.equal(get('#batch-resume').hidden,true);
});
test('interrupted batch offers resume and safely renders filenames',()=>{
  const interrupted=job('INTERRUPTED');
  interrupted.report.batch.items[1].name='<img src=x onerror=alert(1)>.mkv';
  const {get}=setup(interrupted);
  assert.equal(get('#batch-resume').hidden,false);
  assert.doesNotMatch(get('#batch-progress').innerHTML,/<img/);
  assert.match(get('#batch-progress').innerHTML,/&lt;img/);
});
