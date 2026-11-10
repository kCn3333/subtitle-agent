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
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  const output=get('#ai-console');
  assert.equal(output.children.length,1);
  assert.match(output.children[0].textContent,/SYNC/);
  assert.ok(output.children[0].textContent.includes(hostile));
  const unchanged=output.children[0];
  await context.refreshAiConsole();assert.equal(output.children[0],unchanged);
  output.scrollHeight=1000;output.clientHeight=200;output.scrollTop=50;
  entries=[...entries,{...entries[0],id:2,level:'SUMMARY',message:'PODSUMOWANIE OPERACJI'}];
  await context.refreshAiConsole();assert.equal(output.scrollTop,50);
  assert.equal(output.children.length,2);
  assert.match(output.children[1].className,/SUMMARY/);
  await get('#clear-ai-console').handlers.click();
  assert.equal(output.children.length,0);
});
test('connection test metrics remain only in console, with highlighted summary',async()=>{
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,children:[],handlers:{},
    addEventListener(name,handler){this.handlers[name]=handler},querySelectorAll(){return []},
    append(node){this.children.push(node)},replaceChildren(){this.children=[]}});return elements.get(id)};
  const context={document:{querySelector:get,createElement:()=>({}),hidden:false},setInterval(){},
    fetch:async(url)=>({ok:true,json:async()=>url.endsWith('/console')?
      {entries:[{id:1,timestamp:'2026-10-05T00:00:00Z',operation:'TEST',level:'SUMMARY',message:'Tokeny: 42 · Koszt: 0.05 USD'}]}:
      url.endsWith('/test')?{elapsed_seconds:1,usage:{total_tokens:42,cost:0.05,cost_currency:'USD'}}:
      {api_url:'http://test/v1',model:'test',timeout_seconds:600}})};
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  await context.saveAiSettings(true);
  assert.equal(get('#settings-status').textContent,'Połączenie poprawne.');
  assert.match(get('#ai-console').children[0].textContent,/42.*0.05 USD/);
  assert.match(get('#ai-console').children[0].className,/SUMMARY/);
});
test('settings preserve selected reasoning and optional output limit in save payload',async()=>{
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,children:[],addEventListener(){},querySelectorAll(){return []},append(node){this.children.push(node)},replaceChildren(){this.children=[]}});return elements.get(id)};
  let saved;
  const context={document:{querySelector:get,createElement:()=>({}),hidden:false},setInterval(){},
    fetch:async(url,options)=>({ok:true,json:async()=>{
      if(url.endsWith('/save')&&options?.method==='POST'){saved=JSON.parse(options.body).settings;return saved}
      if(url.endsWith('/console'))return {entries:[]};
      return {api_url:'http://test/v1',model:'test',timeout_seconds:600,reasoning_effort:'low',max_output_tokens:131072};
    }})};
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  assert.equal(get('#reasoning-effort').value,'low');
  assert.equal(get('#max-output-tokens').value,131072);
  await context.saveAiSettings(false);
  assert.equal(saved.reasoning_effort,'low');assert.equal(saved.max_output_tokens,131072);
  get('#reasoning-effort').value='';get('#max-output-tokens').value='';
  await context.saveAiSettings(false);
  assert.equal(saved.reasoning_effort,null);assert.equal(saved.max_output_tokens,null);
});

test('connection test follows queued operation without holding the POST response',async()=>{
  const elements=new Map(),messages=[];
  const get=id=>{
    if(!elements.has(id)){
      const el={value:'',checked:false,children:[],addEventListener(){},querySelectorAll(){return []},append(node){this.children.push(node)},replaceChildren(){this.children=[]}};
      if(id==='#settings-status')Object.defineProperty(el,'textContent',{get(){return messages.at(-1)},set(value){messages.push(value)}});
      elements.set(id,el);
    }
    return elements.get(id);
  };
  let polls=0;
  const context={document:{querySelector:get,createElement:()=>({}),hidden:false},setInterval(){},setTimeout(fn){fn()},
    fetch:async(url,options)=>({ok:true,json:async()=>{
      if(url.endsWith('/console'))return {entries:[]};
      if(url==='/api/settings/ai/test')return {status:'queued',operation_id:'test-op',queue_position:2};
      if(url==='/api/ai-operations/test-op')return ++polls===1?{status:'running',operation_id:'test-op'}:{status:'completed',result:{ok:true}};
      if(options?.method==='PUT')return JSON.parse(options.body);
      return {api_url:'http://test/v1',model:'test',timeout_seconds:120};
    }})};
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  await context.saveAiSettings(true);
  assert.equal(polls,2);
  assert.ok(messages.some(message=>/pozycja 2/.test(message)));
  assert.ok(messages.includes('Testowanie połączenia…'));
  assert.equal(messages.at(-1),'Połączenie poprawne.');
});

async function keySettings(){
  const elements=new Map(),requests=[];
  const get=id=>{
    if(!elements.has(id))elements.set(id,{value:'',children:[],handlers:{},
      addEventListener(name,handler){this.handlers[name]=handler},
      querySelectorAll(){return [get('#clear-key'),get('#save-settings')]},
      append(node){this.children.push(node)},replaceChildren(){this.children=[]}});
    return elements.get(id);
  };
  const settings={api_url:'http://local/v1',model:'test',timeout_seconds:120,api_key_set:true};
  const context={document:{querySelector:get,createElement:()=>({}),hidden:false},setInterval(){},failDelete:false,
    fetch:async(url,options)=>{
      if(url.endsWith('/console'))return {ok:true,json:async()=>({entries:[]})};
      if(url.endsWith('/models'))return {ok:true,json:async()=>({models:[],limit:10})};
      if(options?.method==='POST'){
        const payload=JSON.parse(options.body).settings;requests.push(payload);
        if(context.failDelete&&payload.api_key==='')return {ok:false,json:async()=>({detail:{message:'Nie udało się usunąć klucza'}})};
        Object.assign(settings,payload);
        if('api_key' in payload){settings.api_key_set=Boolean(payload.api_key);delete settings.api_key}
      }
      return {ok:true,json:async()=>({...settings})};
    }};
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  return {get,context,requests};
}
test('saved API key is indicated without putting a mask in the submitted value',async()=>{
  const {get,context,requests}=await keySettings();
  assert.equal(get('#api-key').value,'');
  assert.equal(get('#api-key').placeholder,'••••••••');
  assert.equal(get('#clear-key').disabled,false);
  await context.saveAiSettings(false);
  assert.equal('api_key' in requests[0],false);
  get('#api-key').value='replacement-test-key';
  await context.saveAiSettings(false);
  assert.equal(requests[1].api_key,'replacement-test-key');
  assert.equal(get('#api-key').value,'');
  assert.equal(get('#api-key').placeholder,'••••••••');
  const fetch=context.fetch;
  let finish;
  context.fetch=(url,...args)=>url.endsWith('/test')?new Promise(resolve=>finish=resolve):fetch(url,...args);
  const pending=context.saveAiSettings(true);
  await new Promise(setImmediate);
  assert.equal(get('#clear-key').disabled,true);
  finish({ok:true,json:async()=>({status:'completed'})});
  await pending;
  assert.equal(get('#clear-key').disabled,false);
});
test('delete key button preserves saved configuration and form edits and can retry a failed request',async()=>{
  const {get,context,requests}=await keySettings();
  get('#model').value='unsaved-model';
  context.failDelete=true;
  await get('#clear-key').handlers.click();
  assert.equal(get('#api-key').placeholder,'••••••••');
  assert.equal(get('#clear-key').disabled,false);
  assert.equal(get('#settings-status').textContent,'Nie udało się usunąć klucza');
  context.failDelete=false;
  await get('#clear-key').handlers.click();
  assert.deepEqual(requests[1],{api_url:'http://local/v1',model:'test',timeout_seconds:120,api_key:''});
  assert.equal(get('#model').value,'unsaved-model');
  assert.equal(get('#clear-key').disabled,true);
  assert.equal(get('#api-key').placeholder,'Wpisz klucz API');
  assert.match(get('#key-status').textContent,/nie jest ustawiony/);
  await context.saveAiSettings(false);
  assert.equal('api_key' in requests[2],false);
  assert.equal(get('#clear-key').disabled,true);
});
