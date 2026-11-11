const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');

function setup({store=new Map(),permission='granted',secure=true,supported=true,serviceWorker}={}){
  const elements=new Map(),handlers={},windowHandlers={},shown=[],requests=[],states=new Map();
  const get=id=>{
    if(!elements.has(id))elements.set(id,{handlers:{},addEventListener(name,handler){this.handlers[name]=handler}});
    return elements.get(id);
  };
  class Notice{
    static permission=permission;
    static requests=0;
    static async requestPermission(){this.requests++;this.permission='granted';return 'granted'}
    constructor(title,options){shown.push({title,...options,notice:this})}
    close(){this.closed=true}
  }
  const context={Notification:Notice,URLSearchParams,Date,Math,setTimeout,clearTimeout,setInterval(){},
    localStorage:{getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value)},
    document:{querySelector:get,addEventListener:(name,handler)=>handlers[name]=handler},
    window:{isSecureContext:secure,location:{pathname:'/settings',search:'',assign:url=>context.destination=url},
      addEventListener:(name,handler)=>windowHandlers[name]=handler,focus:()=>context.focused=true},
    navigator:serviceWorker?{serviceWorker}:{},
    fetch:async url=>{requests.push(url);const value=states.get(url);return {status:value?200:404,ok:Boolean(value),json:async()=>value}}};
  if(supported)context.window.Notification=Notice;
  runInNewContext(readFileSync('app/static/notifications.js','utf8'),context);
  handlers.DOMContentLoaded();
  return {context,get,shown,states,store,requests,Notice,windowHandlers,api:context.taskNotifications};
}
const preference='subtitleAgentNotifications';

test('notifications require opt-in, permission and HTTPS; test and disable are local',async()=>{
  const ui=setup({permission:'default'});
  assert.equal(ui.Notice.requests,0);
  ui.api.trackJob('before-opt-in');await ui.api.poll();assert.equal(ui.requests.length,0);
  await ui.get('#notifications-toggle').handlers.click();
  assert.equal(ui.Notice.requests,1);
  assert.equal(ui.get('#notifications-test').disabled,false);
  await ui.get('#notifications-test').handlers.click();assert.equal(ui.shown.length,1);
  await ui.get('#notifications-toggle').handlers.click();
  ui.api.trackJob('disabled');await ui.api.poll();assert.equal(ui.requests.length,0);
  assert.equal(ui.get('#notifications-test').disabled,true);
  assert.equal(setup({secure:false}).get('#notifications-toggle').disabled,true);
  assert.equal(setup({supported:false}).get('#notifications-toggle').disabled,true);
  const denied=setup({permission:'denied'});
  assert.equal(denied.get('#notifications-toggle').disabled,true);
  assert.match(denied.get('#notifications-status').textContent,/zablokowane/);
  assert.equal(denied.Notice.requests,0);
});

test('directory completion is notified once, persists across page navigation and opens the task',async()=>{
  const store=new Map([[preference,'true']]);
  const ui=setup({store});
  ui.api.trackJob('season');
  ui.states.set('/api/tasks/season',{status:'RUNNING',displayTitle:'Sezon'});
  await ui.api.poll();assert.equal(ui.shown.length,0);
  ui.states.set('/api/tasks/season',{status:'WORKPACK_READY',displayTitle:'Sezon',report:{batch:{items:[{state:'ready'},{state:'ready'}]}}});
  await ui.api.poll();await ui.api.poll();assert.equal(ui.shown.length,1);
  assert.match(ui.shown[0].title,/Workpack gotowy/);
  ui.shown[0].notice.onclick();assert.equal(ui.context.destination,'/?task=season');
  const restored=setup({store});restored.api.trackJob('season');
  await restored.api.poll();assert.equal(restored.shown.length,0);
});

test('reference pauses notify once per episode; resumed/rebuilt work can complete again',async()=>{
  const ui=setup({store:new Map([[preference,'true']])});ui.api.trackJob('season');
  const paused=current=>({status:'BATCH_PAUSED',displayTitle:'Sezon',report:{batch:{current}}});
  ui.states.set('/api/tasks/season',paused(0));await ui.api.poll();await ui.api.poll();
  assert.equal(ui.shown.length,1);assert.match(ui.shown[0].title,/wybór referencji/);
  ui.states.set('/api/tasks/season',paused(1));await ui.api.poll();assert.equal(ui.shown.length,2);
  ui.api.trackJob('season',true);ui.states.set('/api/tasks/season',{status:'WORKPACK_READY'});
  await ui.api.poll();assert.equal(ui.shown.length,3);
  ui.api.trackJob('season',true);await ui.api.poll();assert.equal(ui.shown.length,4);
});

test('AI operations notify separately and transient failures do not create false results',async()=>{
  const ui=setup({store:new Map([[preference,'true']])});ui.api.trackAi('operation','film');
  await ui.api.poll();assert.equal(ui.shown.length,0); // missing operation is retired
  ui.api.trackAi('new-operation','film');
  const fetch=ui.context.fetch;ui.context.fetch=async()=>{throw Error('network')};
  await ui.api.poll();assert.equal(ui.shown.length,0);
  ui.context.fetch=fetch;
  ui.states.set('/api/ai-operations/new-operation',{status:'failed',mode:'translation',title:'Film'});
  await ui.api.poll();assert.equal(ui.shown.length,1);assert.match(ui.shown[0].title,/Tłumaczenie AI — błąd/);
  assert.equal(ui.shown[0].data.url,'/?task=film');
  ui.api.trackAi('sync','film');ui.states.set('/api/ai-operations/sync',{status:'completed',mode:'sync'});
  await ui.api.poll();assert.equal(ui.shown.length,2);assert.match(ui.shown[1].title,/Synchronizacja AI zakończona/);
});

test('mobile-compatible display uses an active service worker and retries display failures',async()=>{
  let fail=true;const shown=[];
  const ui=setup({store:new Map([[preference,'true']]),serviceWorker:{register:async url=>{
    assert.equal(url,'/notifications-worker.js');
    return {active:true,showNotification:async(title,options)=>{if(fail)throw Error('display unavailable');shown.push({title,...options})}};
  }}});
  ui.api.trackJob('job');ui.states.set('/api/tasks/job',{status:'INSPECTION_READY'});
  await ui.api.poll();assert.equal(shown.length,0);
  fail=false;await ui.api.poll();await ui.api.poll();assert.equal(shown.length,1);
  assert.equal(ui.shown.length,0);
});

test('multiple tabs coordinate notification delivery with a shared browser lock',async()=>{
  const store=new Map([[preference,'true']]);let last=Promise.resolve();
  const locks={request:(name,action)=>{const result=last.then(action);last=result.catch(()=>{});return result}};
  const first=setup({store}),second=setup({store});
  first.context.navigator.locks=second.context.navigator.locks=locks;
  first.api.trackJob('shared');
  for(const ui of [first,second])ui.states.set('/api/tasks/shared',{status:'WORKPACK_READY'});
  await Promise.all([first.api.poll(),second.api.poll()]);
  assert.equal(first.shown.length+second.shown.length,1);
});

test('subscribing to a new task during async display does not lose that task',async()=>{
  let release,started;
  const showing=new Promise(resolve=>started=resolve);
  const ui=setup({store:new Map([[preference,'true']]),serviceWorker:{register:async()=>({active:true,
    showNotification:async()=>{started();await new Promise(resolve=>release=resolve)}
  })}});
  ui.api.trackJob('first');ui.states.set('/api/tasks/first',{status:'WORKPACK_READY'});
  const poll=ui.api.poll();await showing;
  ui.api.trackJob('second');release();await poll;
  assert.ok(JSON.parse(ui.store.get('subtitleAgentNotificationTasks'))['job:second']);
});

test('notification click handler rejects external destinations and opens the selected task',async()=>{
  const handlers={},actions=[];
  const self={location:{origin:'https://app.example'},addEventListener:(name,handler)=>handlers[name]=handler,
    clients:{matchAll:async()=>[],openWindow:async url=>actions.push(url)}};
  runInNewContext(readFileSync('app/static/notifications-worker.js','utf8'),{self,URL});
  async function click(url){let finished;handlers.notificationclick({notification:{data:{url},close(){}},waitUntil:promise=>finished=promise});await finished}
  await click('https://external.example/');assert.equal(actions.length,0);
  await click('/?task=film');assert.deepEqual(actions,['https://app.example/?task=film']);
});
