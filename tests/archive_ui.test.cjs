const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(response){
  const elements=new Map();
  function element(){return {children:[],textContent:'',append(...children){this.children.push(...children)},
    replaceChildren(){this.children=[]},setAttribute(name,value){this[name]=value}}}
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  const context={document:{querySelector:get,createElement:element,createTextNode:text=>({textContent:text})},
    fetch:async()=>response};
  runInNewContext(readFileSync('app/static/archive.js','utf8'),context);
  return {get,context};
}
test('archive displays supplied text safely and creates download links',async()=>{
  const malicious='<img src=x onerror=alert(1)>';
  const file={filename:malicious,sha256:'a'.repeat(64),kind:'translation',url:'/api/archive/files/'+'a'.repeat(64)+'.srt'};
  const {get,context}=setup({ok:true,json:async()=>({titles:[{date:'2026-10-05T12:00:00Z',title:malicious,workpacks:[],subtitles:[file]}]})});
  await context.loadArchive();
  const cells=get('#archive-rows').children[0].children;
  assert.equal(cells[1].textContent,malicious);
  assert.equal(cells[2].textContent,'—');
  const item=cells[3].children[0].children[0];
  assert.equal(item.children[0].href,file.url);
  assert.equal(item.children[0].children[1].textContent,' '+malicious);
  assert.equal(item.children[1].textContent,'Tłumaczenie');
  assert.equal(get('#archive-status').textContent,'Ostatnie tytuły: 1 / 30');
  assert.doesNotMatch(readFileSync('app/static/archive.js','utf8'),/innerHTML/);
});
test('archive presents a readable HTTP error',async()=>{
  const {get,context}=setup({ok:false});await context.loadArchive();
  assert.equal(get('#archive-status').textContent,'Nie udało się wczytać archiwum');
});
