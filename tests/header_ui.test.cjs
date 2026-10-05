const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
test('API badge follows configured reachability, refreshes and handles failure',async()=>{
  const elements=new Map([['#api-health',{}],['#ocr-health',{}]]);
  let state={configured:false,available:false,message:'Configure API'};
  let offline=false;
  const context={document:{querySelector:id=>elements.get(id),hidden:false},setInterval(){},
    fetch:async()=>{if(offline)throw new Error();return {ok:true,json:async()=>state}}};
  runInNewContext(readFileSync('app/static/header.js','utf8'),context);
  await context.refreshHeaderHealth();
  assert.match(elements.get('#api-health').textContent,/NIESKONFIGUROWANE/);
  state={configured:true,available:true,message:'API responds'};
  await context.refreshHeaderHealth();
  assert.equal(elements.get('#api-health').textContent,'● API ONLINE');
  assert.equal(elements.get('#api-health').title,'API responds');
  state={configured:true,available:false};await context.refreshHeaderHealth();
  assert.equal(elements.get('#api-health').textContent,'● API OFFLINE');
  offline=true;await context.refreshHeaderHealth();
  assert.equal(elements.get('#api-health').className,'health health-offline');
});
test('readable episode title removes release tags and preserves diacritics',()=>{
  const context={};runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  assert.equal(context.readableMediaTitle('/media/Zażółć.S02E03.1080p.WEB-DL.mkv'),'Zażółć · S02E03');
  assert.equal(context.readableMediaTitle('/media/Come and See (1985) [Bluray-1080p].mkv'),'Come and See (1985)');
});
