const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(response){
  const elements=new Map();
  function element(){return {hidden:false,disabled:false,options:[],value:'',handlers:{},
    addEventListener(name,handler){this.handlers[name]=handler},
    replaceChildren(){this.options=[];this.value=''},
    append(option){this.options.push(option);if(this.options.length===1)this.value=option.value}}}
  const get=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)};
  get('#reference-source').value='embedded:4';
  const context={document:{querySelector:get,createElement:element},activeJobId:'job',form:get('#job-form'),
    referenceSelect:get('#reference-source'),sourceId:item=>`embedded:${item.streamIndex}`,line(){},
    fetch:(url,options)=>options?.method==='POST'?Promise.resolve(response):new Promise(()=>{})};
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
