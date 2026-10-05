const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(response){
  const elements=new Map();
  function element(){return {hidden:false,disabled:false,options:[],value:'',handlers:{},
    contains(node){return this.options.includes(node)},
    remove(){for(const node of elements.values())node.options=node.options.filter(item=>item!==this)},
    addEventListener(name,handler){this.handlers[name]=handler},
    replaceChildren(){this.options=[];this.value=''},
    append(option){this.options.push(option);if(this.options.length===1)this.value=option.value}}}
  const get=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)};
  get('#reference-source').value='embedded:4';
  const context={output:get('#console'),now:0,document:{querySelector:get,createElement:element},activeJobId:'job',form:get('#job-form'),
    referenceSelect:get('#reference-source'),sourceId:item=>`embedded:${item.streamIndex}`,line(){},
    fetch:(url,options)=>options?.method==='POST'?Promise.resolve(response):new Promise(()=>{})};
  context.Date={now:()=>context.now};
  context.setInterval=callback=>{context.timerCallback=callback;return 1};
  context.clearInterval=()=>{context.timerCallback=null};
  const script=readFileSync('app/static/ai-sync.js','utf8');
  runInNewContext(script,context);
  context.renderAiSync({jobId:'job',status:'WORKPACK_READY',report:{pipeline:'PREPARE_SYNC',
    selectedEnglish:{streamIndex:4},polishCandidates:[{archiveName:'polish/a.srt',originalName:'A'},{archiveName:'polish/b.srt'}]}});
  return {context,get};
}
test('changed EN requires rebuild and disables submission',()=>{
  const {get}=setup();
  assert.equal(get('#ai-sync-panel').hidden,false);
  assert.equal(get('#ai-sync-button').disabled,false);
  assert.equal(get('#ai-sync-button').hidden,false);
  get('#reference-source').value='embedded:13';get('#reference-source').handlers.change();
  assert.equal(get('#ai-sync-button').disabled,true);
  assert.match(get('#ai-sync-status').textContent,/Najpierw zbuduj/);
});
test('successful response shows measured time and tokens',async()=>{
  const {get}=setup({ok:true,json:async()=>({cue_count:2,elapsed_seconds:1.25,
    inputs:[{name:'reference/en.srt'},{name:'polish/a.srt'}],usage:{total_tokens:42}})});
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-download').hidden,false);
  assert.match(get('#ai-sync-status').textContent,/1.25 s/);
  assert.match(get('#ai-sync-status').textContent,/42/);
  get('#ai-polish').handlers.change();
  assert.equal(get('#ai-download').hidden,true);
});
test('invalid response displays error without download',async()=>{
  const {get}=setup({ok:false,json:async()=>({detail:{message:'Niepełna odpowiedź'}})});
  await get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-download').hidden,true);
  assert.equal(get('#ai-sync-status').textContent,'Niepełna odpowiedź');
  assert.equal(get('#ai-sync-button').disabled,false);
});
test('result from different Polish file is not displayed',()=>{
  const {get,context}=setup();
  context.showAiResult({cue_count:1,elapsed_seconds:1,inputs:[{}, {name:'polish/b.srt'}]},'job');
  assert.equal(get('#ai-download').hidden,true);
});

test('only clicking handoff starts console timer, which stops on completion',async()=>{
  let finish;
  const pending=new Promise(resolve=>finish=resolve);
  const {get,context}=setup(pending);
  assert.equal(context.timerCallback,undefined);
  assert.equal(get('#console').options.length,0);
  const operation=get('#ai-sync-button').handlers.click();
  assert.equal(get('#ai-sync-button-label').textContent,'AI pracuje…');
  context.now=65000;context.timerCallback();
  assert.match(get('#console').options[0].textContent,/1 min 05 s/);
  assert.match(get('#ai-sync-status').textContent,/65 s/);
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
