const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
test('AI console renders replies as text, preserves scroll and clears history',async()=>{
  const elements=new Map();
  function element(){return {value:'',children:[],handlers:{},scrollTop:0,scrollHeight:100,clientHeight:100,
    set innerHTML(value){throw new Error('Model replies must never be HTML')},
    addEventListener(name,handler){this.handlers[name]=handler},
    append(node){this.children.push(node)},replaceChildren(){this.children=[]},querySelectorAll(){return []}}}
  const get=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)};
  const hostile='<img src=x onerror=alert(1)> Raw model response';
  let entries=[{id:1,timestamp:'2026-10-04T09:10:21Z',operation:'SYNC',level:'RESPONSE',job_id:'job',message:hostile}];
  const context={document:{querySelector:get,createElement:element,hidden:false},setInterval(){},
    fetch:async(url,options)=>({ok:true,json:async()=>{
      if(url==='/api/settings/ai')return {api_url:'http://local/v1',model:'test',timeout_seconds:120};
      if(options?.method==='DELETE'){entries=[];return {ok:true}}
      return {entries};
    }})};
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  const output=get('#ai-console');
  assert.equal(output.children.length,1);
  assert.match(output.children[0].textContent,/SYNC/);
  assert.ok(output.children[0].textContent.includes(hostile));
  const unchanged=output.children[0];
  await context.refreshAiConsole();assert.equal(output.children[0],unchanged);
  output.scrollHeight=1000;output.clientHeight=200;output.scrollTop=50;
  entries=[...entries,{...entries[0],id:2,message:'Second response'}];
  await context.refreshAiConsole();assert.equal(output.scrollTop,50);
  assert.equal(output.children.length,2);
  await get('#clear-ai-console').handlers.click();
  assert.equal(output.children.length,0);
});
