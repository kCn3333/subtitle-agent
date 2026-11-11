const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
test('only OCR is polled and its badge reflects availability and failure',async()=>{
  const elements=new Map([['#ocr-health',{}]]);
  const urls=[];
  let state={configured:false,available:false,message:'OCR disabled'};
  let offline=false;
  const context={document:{querySelector:id=>elements.get(id),hidden:false},setInterval(){},
    fetch:async(url)=>{urls.push(url);if(offline)throw new Error();return {ok:true,json:async()=>state}}};
  runInNewContext(readFileSync('app/static/header.js','utf8'),context);
  await context.refreshHeaderHealth();
  assert.match(elements.get('#ocr-health').textContent,/WYŁĄCZONY/);
  state={configured:true,available:true,message:'OCR responds'};
  await context.refreshHeaderHealth();
  assert.equal(elements.get('#ocr-health').textContent,'● OCR ONLINE');
  assert.equal(elements.get('#ocr-health').title,'OCR responds');
  state={configured:true,available:false};await context.refreshHeaderHealth();
  assert.equal(elements.get('#ocr-health').textContent,'● OCR OFFLINE');
  offline=true;await context.refreshHeaderHealth();
  assert.equal(elements.get('#ocr-health').className,'health health-offline');
  assert.ok(urls.length>0&&urls.every(url=>url==='/api/workpacks/ocr-health'));
});
test('readable episode title removes release tags and preserves diacritics',()=>{
  const context={};runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  assert.equal(context.readableMediaTitle('/media/Zażółć.S02E03.1080p.WEB-DL.mkv'),'Zażółć · S02E03');
  assert.equal(context.readableMediaTitle('/media/Come and See (1985) [Bluray-1080p].mkv'),'Come and See (1985)');
});

test('mobile navigation toggles, closes on Escape, outside click, navigation and viewport change',()=>{
  const attributes={'aria-expanded':'false'},handlers={},documentHandlers={};
  let open=false,focused=false,viewportChange;
  const toggle={getAttribute:name=>attributes[name],setAttribute:(name,value)=>attributes[name]=value,
    addEventListener:(name,handler)=>handlers[name]=handler,focus:()=>focused=true,contains:target=>target===toggle};
  const nav={classList:{toggle:(name,value)=>open=value},addEventListener:(name,handler)=>handlers['nav-'+name]=handler,contains:target=>target===nav};
  const context={document:{querySelector:id=>({'#nav-toggle':toggle,'#app-navigation':nav})[id],hidden:false,
    addEventListener:(name,handler)=>documentHandlers[name]=handler},setInterval(){},
    window:{matchMedia:()=>({addEventListener:(name,handler)=>viewportChange=handler})}};
  runInNewContext(readFileSync('app/static/header.js','utf8'),context);
  handlers.click();assert.equal(open,true);assert.equal(attributes['aria-expanded'],'true');
  assert.equal(attributes['aria-label'],'Zamknij menu');
  documentHandlers.click({target:nav});assert.equal(open,true);
  documentHandlers.keydown({key:'Escape'});assert.equal(open,false);assert.equal(focused,true);
  assert.equal(attributes['aria-expanded'],'false');assert.equal(attributes['aria-label'],'Otwórz menu');
  handlers.click();documentHandlers.click({target:{}});assert.equal(open,false);
  handlers.click();handlers['nav-click']({target:{closest:()=>({})}});assert.equal(open,false);
  handlers.click();viewportChange();assert.equal(open,false);assert.equal(attributes['aria-expanded'],'false');
});
