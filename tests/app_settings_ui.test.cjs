const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {test}=require('node:test');
async function setup(failLoad=false){
  const inputs=[
    {type:'number',dataset:{setting:'workpack_max_archive_bytes',bytesPerUnit:'1048576'},value:''},
    {type:'number',dataset:{setting:'ffmpeg_timeout_seconds'},value:''},
    {type:'checkbox',dataset:{setting:'include_graphic_reference'},checked:false},
    {type:'url',dataset:{setting:'ocr_worker_url'},value:''},
    {type:'number',dataset:{setting:'archive_max_titles'},value:''}];
  const button={},status={},form={handlers:{},querySelectorAll:selector=>selector==='input[data-setting]'?inputs:[...inputs,button],
    addEventListener(event,handler){this.handlers[event]=handler},reportValidity(){return true}};
  const settings={workpack_max_archive_bytes:104857601,ffmpeg_timeout_seconds:600,include_graphic_reference:true,ocr_worker_url:null,archive_max_titles:30};
  const context={document:{querySelector:selector=>selector==='#app-settings'?form:status},requests:[],failSave:false,
    async settingsRequest(url,options){
      if(!options&&failLoad)throw Error('Odczyt niedostępny');
      if(options){context.requests.push(JSON.parse(options.body));if(context.failSave)throw Error('Zapis niedostępny');return JSON.parse(options.body)}
      return settings;
    }};
  runInNewContext(readFileSync('app/static/app-settings.js','utf8'),context);
  await new Promise(setImmediate);
  return {inputs,button,status,form,context};
}
test('application settings preserve bytes and types, save independently and retain failed edits',async()=>{
  const {inputs,button,status,form,context}=await setup();
  assert.equal(button.disabled,false);
  assert.equal(inputs[0].value*1048576,104857601);
  assert.equal(inputs[2].checked,true);
  inputs[1].value='42';inputs[2].checked=false;inputs[3].value='';inputs[4].value='12';
  context.failSave=true;
  await form.handlers.submit({preventDefault(){}});
  assert.equal(inputs[1].value,'42');assert.equal(status.textContent,'Zapis niedostępny');
  assert.equal(button.disabled,false);
  context.failSave=false;
  await form.handlers.submit({preventDefault(){}});
  assert.deepEqual(context.requests[1],{workpack_max_archive_bytes:104857601,ffmpeg_timeout_seconds:42,include_graphic_reference:false,ocr_worker_url:null,archive_max_titles:12});
  assert.match(status.textContent,/zapisane/);
});
test('application settings cannot overwrite saved configuration after a failed initial load',async()=>{
  const {button,status,form,context}=await setup(true);
  assert.equal(button.disabled,true);
  assert.match(status.textContent,/Odczyt niedostępny/);
  await form.handlers.submit({preventDefault(){}});
  assert.equal(context.requests.length,0);
});
