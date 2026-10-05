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
  assert.equal(cells.length,4);
  assert.equal(cells[3].textContent,'—');
  assert.equal(cells[0].children[0].dateTime,'2026-10-05T12:00:00Z');
  assert.equal(cells[0].children[1].textContent,malicious);
  assert.equal(cells[1].textContent,'—');
  const item=cells[2].children[0].children[0];
  assert.equal(item.children[0].href,file.url);
  assert.equal(item.children[0].children[1].textContent,' Tłumaczenie SRT · aaaaaaaa');
  assert.equal(item.children[0].download,malicious);
  assert.ok(item.children[0].title.includes(malicious));
  assert.equal(get('#archive-status').textContent,'');
  assert.equal(get('#archive-status').hidden,true);
  assert.doesNotMatch(readFileSync('app/static/archive.js','utf8'),/innerHTML/);
});
test('archive presents a readable HTTP error',async()=>{
  const {get,context}=setup({ok:false});await context.loadArchive();
  assert.equal(get('#archive-status').textContent,'Nie udało się wczytać archiwum');
});
test('archive cost shows cumulative USD and tokens, distinguishing unknown and partial sums',()=>{
  const {context}=setup({ok:true,json:async()=>({titles:[]})});
  const cell={children:[],append(...nodes){this.children.push(...nodes)}};
  context.archiveCost(cell,{requests:3,usd:'0.300125',total_tokens:12345,usd_partial:false,tokens_partial:false});
  assert.equal(cell.children[0].textContent,'0,300125 USD');
  assert.equal(cell.children[1].textContent,`${(12345).toLocaleString('pl-PL')} tokenów`);
  const partial={children:[],append(...nodes){this.children.push(...nodes)}};
  context.archiveCost(partial,{requests:2,usd:'0',total_tokens:10,usd_partial:true,tokens_partial:true});
  assert.equal(partial.children[0].textContent,'≥ 0 USD');
  assert.equal(partial.children[1].textContent,'≥ 10 tokenów');
  const unknown={append(...nodes){this.children=nodes}};
  context.archiveCost(unknown,{requests:1,usd:null,total_tokens:null,usd_partial:true,tokens_partial:true});
  assert.equal(unknown.children[0].textContent,'— USD');
  assert.equal(unknown.children[1].textContent,'— tokenów');
});
