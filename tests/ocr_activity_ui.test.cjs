const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {createContext, runInContext} = require('node:vm');
const {test} = require('node:test');

function setup() {
  let now = Date.parse('2026-09-30T11:00:00Z');
  let nextTimer = 1;
  const timers = new Map(), elements = new Map(), connections = [];
  function element() {
    return {style:{}, attributes:{}, children:[], parent:null,
      append(...nodes) {for(const node of nodes){node.remove();this.children.push(node);node.parent=this}},
      contains(node) {return this.children.includes(node)},
      remove() {if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);this.parent=null},
      replaceChildren() {for(const node of [...this.children])node.remove()},
      setAttribute(key,value) {this.attributes[key]=value}};
  }
  class EventSource {
    constructor() {this.listeners={};connections.push(this)}
    addEventListener(name,callback) {this.listeners[name]=callback}
    close() {this.closed=true}
    send(stage,progress,timestamp='2026-09-30T11:00:00Z') {
      this.listeners.job({data:JSON.stringify({stage,progress,timestamp,message:stage,level:'INFO'})});
    }
  }
  const context = createContext({
    document:{querySelector(selector) {
      if(!elements.has(selector))elements.set(selector,element());
      return elements.get(selector);
    },createElement:element},
    Date:class extends Date {static now() {return now}},
    setInterval(callback) {const id=nextTimer++;timers.set(id,callback);return id},
    clearInterval(id) {timers.delete(id)},
    requestAnimationFrame(callback) {callback()},
    EventSource, localStorage:{setItem(){}}, fetch:async()=>({ok:false}),
  });
  const script=readFileSync('app/static/app.js','utf8').split("form.addEventListener('submit'")[0];
  runInContext(script,context);
  elements.set('#ocr-activity',runInContext('ocrActivity',context));
  elements.set('#ocr-activity-label',runInContext('ocrActivityLabel',context));
  elements.set('#ocr-elapsed',runInContext('ocrElapsed',context));
  runInContext("connect('job-1')",context);
  const connection=connections[0];
  connection.listeners.open();
  return {elements,timers,connection,context,connections,
    advance(seconds) {now+=seconds*1000;for(const callback of timers.values())callback()}};
}

test('OCR events display elapsed time without inventing completion percentage',()=>{
  const ui=setup();
  ui.connection.send('OCR_RUNNING',72,'2026-09-30T10:58:30Z');
  assert.equal(ui.elements.get('#ocr-activity').parent,ui.elements.get('#console'));
  assert.equal(ui.elements.get('#ocr-activity-label').textContent,'[OCR_RUNNING] OCR w toku…');
  assert.equal(ui.elements.get('#ocr-elapsed').textContent,' · czas oczekiwania: 1 min 30 s');
  ui.advance(5);
  assert.equal(ui.elements.get('#ocr-elapsed').textContent,' · czas oczekiwania: 1 min 35 s');
  assert.equal(ui.elements.get('#progress').textContent,'72%');
  ui.connection.send('OCR_RUNNING',72);
  assert.equal(ui.timers.size,1);
  assert.equal(ui.elements.get('#ocr-elapsed').textContent,' · czas oczekiwania: 1 min 35 s');
});

test('connection loss pauses confirmation without resetting elapsed time',()=>{
  const ui=setup();
  ui.connection.send('OCR_RUNNING',72);
  ui.connection.onerror();
  assert.equal(ui.elements.get('#ocr-activity').attributes['data-connected'],'false');
  assert.match(ui.elements.get('#ocr-activity-label').textContent,/niepotwierdzony/);
  ui.advance(10);
  ui.connection.listeners.open();
  assert.equal(ui.elements.get('#ocr-activity').attributes['data-connected'],'true');
  assert.match(ui.elements.get('#ocr-elapsed').textContent,/0 min 10 s/);
});

for(const [stage,progress] of [['OCR_RUNNING',75],['BUILDING_MANIFEST',78],['NEEDS_OCR',100],['FAILED',100],['WORKPACK_READY',100]]) {
  test(`${stage} at ${progress} stops the OCR timer`,()=>{
    const ui=setup();
    ui.connection.send('OCR_RUNNING',72);
    ui.connection.send(stage,progress);
    assert.equal(ui.elements.get('#ocr-activity').parent,null);
    assert.equal(ui.timers.size,0);
  });
}

test('new task clears OCR indicator and ignores old connection events',()=>{
  const ui=setup();
  ui.connection.send('OCR_RUNNING',72);
  runInContext("connect('job-2')",ui.context);
  ui.connection.send('OCR_RUNNING',72);
  assert.equal(ui.elements.get('#ocr-activity').parent,null);
  assert.equal(ui.timers.size,0);
  assert.equal(ui.connection.closed,true);
});


test('clearing console retains one live OCR row and timer',()=>{
  const ui=setup();
  ui.connection.send('OCR_RUNNING',72);
  runInContext('clearConsole()',ui.context);
  assert.deepEqual(ui.elements.get('#console').children,[ui.elements.get('#ocr-activity')]);
  ui.advance(3);
  assert.match(ui.elements.get('#ocr-elapsed').textContent,/0 min 03 s/);
  assert.equal(ui.timers.size,1);
});

test('template has no separate OCR activity panel',()=>{
  const html=readFileSync('app/templates/index.html','utf8');
  assert.doesNotMatch(html,/id="ocr-activity"|id="ocr-elapsed"|ocr-spinner/);
});
