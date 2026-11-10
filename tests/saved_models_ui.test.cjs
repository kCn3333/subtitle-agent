const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
async function setup(){
  const elements=new Map(),requests=[];
  const blank={api_url:'http://provider/v1',model:'first',timeout_seconds:120,api_key_set:true};
  let models=[{id:'first-id',...blank}],active={...blank};
  const get=id=>{
    if(!elements.has(id))elements.set(id,{value:'',children:[],handlers:{},
      reportValidity(){return true},addEventListener(event,handler){this.handlers[event]=handler},
      append(node){this.children.push(node)},replaceChildren(){this.children=[]},
      querySelectorAll(){return ['#save-model','#delete-model','#test-api','#clear-key'].map(get)}});
    return elements.get(id);
  };
  const context={document:{querySelector:get,createElement:()=>({}),hidden:false},setInterval(){},
    fetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(options?.method)requests.push({url,method:options.method,body});
      let result;
      if(url.endsWith('/console'))result={entries:[]};
      else if(url.endsWith('/models')&&!options?.method)result={models,limit:10,active_id:models.find(row=>row.model===active.model)?.id};
      else if(url.endsWith('/test'))result={status:'completed'};
      else if(url.includes('/models/')&&options?.method==='DELETE'){models=models.filter(row=>!url.includes(row.id));result={deleted:true}}
      else if(url.includes('/models/')&&url.endsWith('/activate')){active={...models.find(row=>url.includes(row.id))};delete active.id;result=active}
      else if(url.endsWith('/save')){active={...body.settings,api_key_set:body.settings.api_key?true:active.api_key_set};delete active.api_key;result=active}
      else if(url.endsWith('/models')&&options?.method==='POST'){
        const id=body.model_id||'new-id';const row={id,...body.settings,api_key_set:active.api_key_set};
        delete row.api_key;models=models.filter(model=>model.id!==id);models.push(row);active={...row};delete active.id;
        result={id,settings:active};
      }else result=active;
      return {ok:true,json:async()=>result};
    }};
  runInNewContext(readFileSync('app/static/settings-request.js','utf8'),context);
  runInNewContext(readFileSync('app/static/settings.js','utf8'),context);
  await new Promise(setImmediate);
  return {get,requests,active:()=>active};
}
test('AI form tests drafts, persists settings separately and adds models without overwriting selection',async()=>{
  const {get,requests,active}=await setup();
  assert.equal(get('#saved-model').value,'first-id');
  assert.equal(get('#delete-model').disabled,false);
  get('#saved-model').value='';await get('#saved-model').handlers.change();
  assert.equal(get('#delete-model').disabled,true);
  get('#api-url').value='http://other/v1';get('#model').value='draft';get('#api-key').value='draft-key';
  await get('#test-api').handlers.click();
  assert.equal(active().model,'first');
  assert.equal(requests.at(-1).url,'/api/settings/ai/test');
  assert.equal(requests.at(-1).body.settings.api_key,'draft-key');
  assert.equal(requests.at(-1).body.key_source_id,null);
  await get('#ai-settings').handlers.submit({preventDefault(){}});
  assert.equal(active().model,'draft');
  assert.equal(requests.filter(row=>row.url==='/api/settings/ai/models').length,0);
  assert.equal(get('#saved-model').value,'');
  assert.equal(get('#api-key').value,'');
  await get('#save-model').handlers.click();
  assert.equal(get('#saved-model').value,'new-id');
  assert.match(get('#saved-model-count').textContent,/2\/10/);
  get('#saved-model').value='first-id';await get('#saved-model').handlers.change();
  assert.equal(get('#model').value,'first');
  assert.equal(get('#api-key').value,'');
  assert.equal(get('#api-key').placeholder,'••••••••');
  get('#timeout').value='240';
  await get('#ai-settings').handlers.submit({preventDefault(){}});
  assert.equal(requests.at(-1).body.model_id,'first-id');
  assert.equal(requests.at(-1).body.settings.timeout_seconds,240);
  get('#model').value='another';
  await get('#save-model').handlers.click();
  assert.equal(requests.at(-1).body.model_id,null);
  get('#saved-model').value='first-id';await get('#saved-model').handlers.change();
  get('#api-url').value='http://unrelated/v1';get('#api-url').handlers.input();
  assert.equal(get('#api-key').placeholder,'Wpisz klucz API');
  await get('#delete-model').handlers.click();
  assert.equal(get('#saved-model').value,'');
  assert.equal(get('#delete-model').disabled,true);
  assert.match(get('#saved-model-count').textContent,/1\/10/);
  assert.equal(active().model,'first');
});
