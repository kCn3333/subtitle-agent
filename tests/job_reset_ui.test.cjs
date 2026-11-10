const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(){
  const elements=new Map(),storage=new Map(),connections=[];
  function element(){return {hidden:false,disabled:false,value:'',innerHTML:'',style:{},handlers:{},children:[],options:[],
    querySelector:selector=>selector==='button'?get('#submit-job'):null,addEventListener(event,handler){(this.handlers[event]??=[]).push(handler)},
    setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},remove(){},
    append(node){this.children.push(node);this.options.push(node)},contains(node){return this.children.includes(node)},
    replaceChildren(){this.children=[];this.options=[];this.innerHTML=''},
    get outerHTML(){return String(this.textContent??'')}}}
  function get(selector){if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)}
  const context={document:{querySelector:get,createElement:element},requestAnimationFrame:callback=>callback(),
    setInterval(){return 1},clearInterval(){},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    EventSource:class{constructor(){this.listeners={};connections.push(this)}addEventListener(event,handler){this.listeners[event]=handler}close(){this.closed=true}},
    hideAiHandoff(){get('#ai-sync-panel').hidden=true;get('#ai-sync-button').hidden=true},
    fetch(){throw Error('Unexpected request')}};
  runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  runInNewContext(readFileSync('app/static/app.js','utf8'),context);
  runInNewContext(readFileSync('app/static/directory-batch.js','utf8'),context);
  context.previous={jobId:'old-season',status:'WORKPACK_READY',displayTitle:'Old season',report:{jobType:'DIRECTORY_WORKPACK',
    batch:{items:[{name:'Show.S01E01.mkv',state:'ready'}]},workpack:{filename:'season.zip'}}};
  runInNewContext('render(previous);setProgress(100);connect(previous.jobId)',context);
  get('#ai-batch-progress').hidden=false;get('#ai-batch-progress').innerHTML='Old AI episodes';get('#ai-sync-panel').hidden=false;
  get('#task-type').value='INSPECT';get('#media-path').value='/media/Movie.mkv';
  return {context,get,connections,storage};
}
test('starting a film clears previous episode lists before POST completes and ignores stale task responses',async()=>{
  const {context,get,connections,storage}=setup();
  let finishOld,finishPost;
  context.fetch=(url,options)=>new Promise(resolve=>{if(options?.method==='POST')finishPost=resolve;else finishOld=resolve});
  const oldLoad=context.load('old-season');
  const pending=get('#job-form').handlers.submit[0]({preventDefault(){}});
  assert.equal(connections[0].closed,true);
  for(const selector of ['#batch-progress','#ai-batch-progress']){
    assert.equal(get(selector).hidden,true);assert.equal(get(selector).innerHTML,'');
  }
  assert.equal(get('#ai-sync-panel').hidden,true);
  assert.equal(get('#progress').textContent,'0%');
  assert.equal(storage.has('subtitleAgentWorkpackJobId'),false);
  finishOld({ok:true,json:async()=>context.previous});await oldLoad;
  assert.equal(get('#batch-progress').hidden,true);
  assert.equal(get('#status').textContent,'OCZEKIWANIE');
  finishPost({ok:true,json:async()=>({jobId:'new-film',jobType:'PREPARE_WORKPACK',displayTitle:'Movie'})});await pending;
  assert.equal(get('#batch-progress').hidden,true);
  assert.equal(storage.get('subtitleAgentWorkpackJobId'),'new-film');
  assert.equal(connections.length,2);
});
test('rejected new task does not restore the previous season view or download',async()=>{
  const {context,get}=setup();
  context.fetch=async()=>({ok:false,json:async()=>({detail:{message:'Nieprawidłowa ścieżka'}})});
  await get('#job-form').handlers.submit[0]({preventDefault(){}});
  assert.equal(get('#batch-progress').hidden,true);
  assert.equal(get('#ai-batch-progress').hidden,true);
  assert.equal(get('#download')['aria-disabled'],'true');
  assert.equal(get('#submit-job').disabled,false);
});
