async function refreshHeaderHealth(){
  const ocr=document.querySelector('#ocr-health');
  try{
    const response=await fetch('/api/workpacks/ocr-health');if(!response.ok)throw new Error();
    const state=await response.json();ocr.className=`health ${state.available?'health-online':state.configured?'health-offline':'health-disabled'}`;
    ocr.textContent=state.available?'● OCR ONLINE':state.configured?'● OCR OFFLINE':'● OCR WYŁĄCZONY';
  }catch{ocr.className='health health-offline';ocr.textContent='● OCR OFFLINE'}
}
refreshHeaderHealth();setInterval(refreshHeaderHealth,30000);
