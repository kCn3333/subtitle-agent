const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
function setup(clipboard,execResult=true){
  const controls=['console','ai-console'].map(id=>({dataset:{copyConsole:id},handlers:{},
    after(node){this.feedback=node},addEventListener(name,handler){this.handlers[name]=handler}}));
  const nodes=new Map([['console',{children:[{textContent:'[INFO] First'},{textContent:'[ERROR] Second'}]}],
    ['ai-console',{children:[{textContent:'[RESPONSE]\n{"segments":[]}\n'}]}]]);
  let field,removed=false,restored=false,execCalls=0;
  const context={navigator:{clipboard},document:{activeElement:{focus(){restored=true}},getSelection:()=>null,
    querySelectorAll:()=>controls,getElementById:id=>nodes.get(id),body:{append(node){field=node}},
    createElement:()=>({setAttribute(){},select(){},remove(){removed=true}}),
    execCommand(command){assert.equal(command,'copy');execCalls++;return execResult}}};
  runInNewContext(readFileSync('app/static/console-tools.js','utf8'),context);
  return {controls,context,state:()=>({field,removed,restored,execCalls})};
}
test('copy sends complete visible log including line breaks to clipboard',async()=>{
  const copied=[];const {controls}=setup({writeText:async text=>copied.push(text)});
  await controls[0].handlers.click();await controls[1].handlers.click();
  assert.deepEqual(copied,['[INFO] First\n[ERROR] Second','[RESPONSE]\n{"segments":[]}\n']);
  assert.equal(controls[0].feedback.textContent,'Skopiowano zawartość konsoli.');
});
test('copy works on plain HTTP without Clipboard API and restores focus',async()=>{
  const {controls,state}=setup(undefined);await controls[0].handlers.click();
  assert.equal(state().field.value,'[INFO] First\n[ERROR] Second');
  assert.equal(state().execCalls,1);assert.ok(state().removed&&state().restored);
});
test('denied Clipboard API falls back and failed copying is not reported as success',async()=>{
  const {controls,state}=setup({writeText:async()=>{throw new Error('Denied')}},false);
  await controls[1].handlers.click();assert.equal(state().execCalls,1);
  assert.match(controls[1].feedback.textContent,/Nie udało się/);
  assert.ok(state().removed&&state().restored);
});
