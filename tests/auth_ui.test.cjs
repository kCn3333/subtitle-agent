const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');

test('authenticated fetch attaches CSRF only to same-origin mutations and redirects on expiry',async()=>{
  const calls=[],redirects=[];
  let status=200;
  const window={location:{href:'https://app.example/',origin:'https://app.example',replace:url=>redirects.push(url)},
    fetch:async(input,options)=>{calls.push({input,options});return {status}}};
  const document={querySelector:()=>({content:'csrf-secret'}),addEventListener(){}};
  runInNewContext(readFileSync('app/static/auth.js','utf8'),{window,document,URL,Headers});
  await window.fetch('/api/settings/ai',{method:'PUT',headers:{'Content-Type':'application/json'},body:'{}'});
  assert.equal(calls.at(-1).options.headers.get('X-CSRF-Token'),'csrf-secret');
  assert.equal(calls.at(-1).options.headers.get('Content-Type'),'application/json');
  assert.equal(calls.at(-1).options.body,'{}');
  await window.fetch('/api/archive');
  assert.equal(calls.at(-1).options.headers.has('X-CSRF-Token'),false);
  await window.fetch('https://other.example/',{method:'POST'});
  assert.equal(calls.at(-1).options.headers.has('X-CSRF-Token'),false);
  status=401;
  await window.fetch('/api/archive');
  assert.deepEqual(redirects,['/login']);
});

test('login clears rejected password, reports lockout and redirects after success',async()=>{
  const elements=new Map(),redirects=[],calls=[];
  function get(selector){
    if(!elements.has(selector))elements.set(selector,{value:'',content:'login-csrf',querySelector:()=>get('button'),addEventListener(name,handler){this[name]=handler}});
    return elements.get(selector);
  }
  let ok=false;
  const context={document:{querySelector:get},window:{location:{replace:url=>redirects.push(url)}},
    fetch:async(url,options)=>{calls.push({url,options});return {ok,status:ok?200:429,
      headers:{get:()=> '3600'},json:async()=>({detail:{message:'Adres IP jest czasowo zablokowany.'}})}}};
  runInNewContext(readFileSync('app/static/login.js','utf8'),context);
  get('#username').value='admin';get('#password').value='example-test-password';
  await get('#login-form').submit({preventDefault(){}});
  assert.equal(get('#password').value,'');
  assert.match(get('#login-status').textContent,/60 min/);
  assert.equal(get('button').disabled,false);
  assert.equal(calls[0].options.headers['X-CSRF-Token'],'login-csrf');
  ok=true;get('#password').value='example-test-password';
  await get('#login-form').submit({preventDefault(){}});
  assert.deepEqual(redirects,['/']);
});
