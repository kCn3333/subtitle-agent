// Notifications are driven by open pages, without Web Push or background polling in a worker.
var taskNotifications=(()=>{
  const preference='subtitleAgentNotifications',watchKey='subtitleAgentNotificationTasks';
  let polling=false,registrationPromise=null,requesting=false,permissionAttempted=false;
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback}catch{return fallback}};
  const write=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));return true}catch{return false}};
  const supported=()=>window.isSecureContext&&'Notification' in window;
  const wanted=()=>read(preference,true)===true;
  const enabled=()=>supported()&&Notification.permission==='granted'&&wanted();
  const withLock=action=>navigator.locks?navigator.locks.request('subtitle-agent-notifications',action):action();
  const taskUrl=id=>`/?task=${encodeURIComponent(id)}`;

  async function registration(){
    if(!('serviceWorker' in navigator))return null;
    if(!registrationPromise)registrationPromise=navigator.serviceWorker.register('/notifications-worker.js').then(async reg=>{
      if(reg.active)return reg;
      const worker=reg.installing||reg.waiting;
      if(!worker)throw Error('Brak aktywnego workera powiadomień');
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Nie udało się uruchomić powiadomień')),10000);
        const changed=()=>{
          if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve()}
          else if(worker.state==='redundant'){clearTimeout(timer);reject(Error('Nie udało się uruchomić powiadomień'))}
        };
        worker.addEventListener('statechange',changed);changed();
      });return reg;
    }).catch(error=>{registrationPromise=null;throw error});
    return registrationPromise;
  }
  async function show(title,body,tag,jobId){
    const options={body,tag,icon:'/static/apple-touch-icon.png',data:{url:jobId?taskUrl(jobId):'/settings'}};
    const reg=await registration();
    if(reg){await reg.showNotification(title,options);return}
    const notification=new Notification(title,options);
    notification.onclick=()=>{window.focus();window.location.assign(options.data.url);notification.close()};
  }
  function controls(){
    const input=document.querySelector('#notifications-toggle');if(!input)return;
    input.disabled=!supported()||Notification.permission==='denied';
    input.checked=wanted()&&supported()&&Notification.permission!=='denied';
    input.title=!window.isSecureContext?'Powiadomienia wymagają HTTPS.':
      !('Notification' in window)?'Ta przeglądarka nie obsługuje powiadomień.':
      Notification.permission==='denied'?'Zezwól na powiadomienia w ustawieniach witryny w przeglądarce.':
      Notification.permission==='default'?'Przeglądarka poprosi o zgodę przy uruchomieniu zadania.':'';
  }
  async function requestAccess(){
    if(!supported()||!wanted()||Notification.permission!=='default'||requesting||permissionAttempted)return;
    requesting=true;permissionAttempted=true;
    try{
      // Called from a task-start click or the switch's change handler.
      await Notification.requestPermission();
      if(enabled()){await restoreActive();poll()}
    }catch{/* Browser permission failures must not interrupt a task. */}
    finally{requesting=false;controls()}
  }
  async function toggle(){
    const input=document.querySelector('#notifications-toggle');
    if(!supported())return;
    const selected=input.checked;
    write(preference,selected);
    if(!selected){write(watchKey,{});controls();return}
    permissionAttempted=false;
    await requestAccess();
    controls();if(enabled())await restoreActive();poll();
  }
  async function restoreActive(){
    try{
      const id=localStorage.getItem("subtitleAgentWorkpackJobId");if(!id)return;
      const response=await fetch(`/api/tasks/${encodeURIComponent(id)}`);
      if(response.ok){const state=await response.json(),result=outcome({kind:"job"},state);if(!result||!result.done)track("job",id,id)}
      const ai=await fetch(`/api/tasks/${encodeURIComponent(id)}/ai-status`);
      if(ai.ok){const state=await ai.json();if(["queued","running"].includes(state.status))track("ai",state.operation_id,id)}
    }catch{}
  }
  function track(kind,id,jobId,restart=false){
    if(!id||!supported()||!wanted()||(Notification.permission!=='granted'&&!requesting))return;
    const tasks=read(watchKey,{}),key=`${kind}:${id}`;
    if(tasks[key]&&!restart)return;
    tasks[key]={kind,id,jobId,cycle:`${Date.now()}-${Math.random()}`,seen:null,done:false};
    // Bound history, retaining every unfinished task.
    const done=Object.keys(tasks).filter(key=>tasks[key].done);
    for(const key of done.slice(0,Math.max(0,Object.keys(tasks).length-100)))delete tasks[key];
    write(watchKey,tasks);
  }
  function outcome(task,state){
    const title=state.displayTitle||state.title||'Zadanie';
    if(task.kind==='ai'){
      const operation=state.mode==='translation'?'Tłumaczenie AI':'Synchronizacja AI';
      if(state.status==='completed')return {key:'completed',done:true,title:`${operation} ${state.mode==='translation'?'zakończone':'zakończona'}`,body:title};
      if(state.status==='failed')return {key:'failed',done:true,title:`${operation} — błąd`,body:title};
      return null;
    }
    const report=state.report||{};
    const confirmation=report.externalReferenceConfirmationRequired||report.workpack?.referenceAmbiguous;
    if(state.status==='BATCH_PAUSED'||(['WORKPACK_READY','WORKPACK_INCOMPLETE'].includes(state.status)&&confirmation)){
      return {key:`attention:${report.batch?.current??''}`,done:false,title:'Wymagany wybór referencji',body:`${title} — potwierdź wzorzec lub pomiń plik.`};
    }
    if(['FAILED','INTERRUPTED'].includes(state.status))return {key:state.status,done:true,title:'Zadanie wymaga uwagi',body:`${title} — ${state.status==='FAILED'?'błąd przetwarzania':'praca przerwana'}.`};
    if(state.status==='NEEDS_OCR')return {key:state.status,done:true,title:'Wymagane OCR referencji',body:title};
    if(state.status==='WORKPACK_INCOMPLETE')return {key:state.status,done:true,title:'Workpack zakończony z brakami',body:title};
    if(state.status==='WORKPACK_READY')return {key:state.status,done:true,title:'Workpack gotowy',body:title};
    if(state.status==='INSPECTION_READY')return {key:state.status,done:true,title:'Analiza zakończona',body:title};
    return null;
  }
  async function poll(){
    if(polling||!enabled())return;
    polling=true;
    try{
      for(const [key,task] of Object.entries(read(watchKey,{}))){
        if(task.done)continue;
        try{
          const response=await fetch(task.kind==='ai'?`/api/ai-operations/${encodeURIComponent(task.id)}`:`/api/tasks/${encodeURIComponent(task.id)}`);
          if(response.status===401)return;
          const state=response.ok?await response.json():null;
          if(!state&&![404,410].includes(response.status))continue;
          await withLock(async()=>{
            if(!enabled())return;
            const tasks=read(watchKey,{}),current=tasks[key];
            if(!current||current.cycle!==task.cycle||current.done)return;
            if(!state){current.done=true;write(watchKey,tasks);return}
            const result=outcome(task,state);
            if(!result)return;
            if(current.seen!==result.key){
              await show(`Subtitle Agent · ${result.title}`,result.body,`${key}:${task.cycle}:${result.key}`,task.jobId);
            }
            // Display is asynchronous: preserve subscriptions created while it was in flight.
            const latest=read(watchKey,{}),entry=latest[key];
            if(entry&&entry.cycle===task.cycle){entry.seen=result.key;entry.done=result.done;write(watchKey,latest)}
          });
        }catch{/* Network/display failures never interrupt processing; retry on the next poll. */}
      }
    }finally{polling=false}
  }
  function setup(){
    document.querySelector('#notifications-toggle')?.addEventListener('change',toggle);
    controls();poll();
  }
  document.addEventListener('click',event=>{
    if(event.target.closest?.('#job-form button, #ai-sync-button'))requestAccess();
  },true);
  // This script loads before app.js, including when a task notification was clicked.
  const requested=new URLSearchParams(window.location.search).get('task');
  if(window.location.pathname==='/'&&requested&&/^[a-zA-Z0-9_-]{1,80}$/.test(requested)){
    try{localStorage.setItem('subtitleAgentWorkpackJobId',requested)}catch{}
  }
  document.addEventListener('DOMContentLoaded',setup);
  window.addEventListener('storage',()=>{controls();poll()});
  window.addEventListener('focus',()=>{controls();poll()});
  setInterval(poll,5000);
  return {trackJob:(id,restart=false)=>track('job',id,id,restart),trackAi:(id,jobId)=>track('ai',id,jobId),poll};
})();
