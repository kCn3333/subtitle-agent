const appSettingsForm=document.querySelector('#app-settings'),appSettingsStatus=document.querySelector('#app-settings-status');
let appSettingsLoaded=false;
function appSettingsBusy(busy){
  appSettingsForm.querySelectorAll('input, button').forEach(element=>element.disabled=busy||!appSettingsLoaded);
}
function displayAppSettings(settings){
  for(const input of appSettingsForm.querySelectorAll('input[data-setting]')){
    const value=settings[input.dataset.setting];
    if(input.type==='checkbox')input.checked=value;
    else input.value=input.dataset.bytesPerUnit?value/Number(input.dataset.bytesPerUnit):value??'';
  }
}
function readAppSettings(){
  const settings={};
  for(const input of appSettingsForm.querySelectorAll('input[data-setting]')){
    settings[input.dataset.setting]=input.type==='checkbox'?input.checked:
      input.type==='number'?(input.dataset.bytesPerUnit?Math.round(Number(input.value)*Number(input.dataset.bytesPerUnit)):Number(input.value)):
      input.value.trim()||null;
  }
  return settings;
}
appSettingsForm.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!appSettingsLoaded||!appSettingsForm.reportValidity())return;
  appSettingsBusy(true);appSettingsStatus.textContent='Zapisywanie ustawień aplikacji…';
  try{
    const settings=await settingsRequest('/api/settings/app',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(readAppSettings())});
    displayAppSettings(settings);
    appSettingsStatus.textContent='Ustawienia aplikacji zapisane. Nowe zadania użyją nowej konfiguracji. Limity archiwum i listy modeli obowiązują od razu.';
    if(typeof refreshHeaderHealth==='function')refreshHeaderHealth();
  }catch(error){appSettingsStatus.textContent=error.message}finally{appSettingsBusy(false)}
});
appSettingsBusy(true);
settingsRequest('/api/settings/app').then(settings=>{
  displayAppSettings(settings);appSettingsLoaded=true;
}).catch(error=>appSettingsStatus.textContent=`Nie udało się odczytać ustawień aplikacji: ${error.message}`)
  .finally(()=>appSettingsBusy(false));
