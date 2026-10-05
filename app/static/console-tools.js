// The HTTP LAN deployment also needs copying when the secure Clipboard API is unavailable.
async function copyConsoleText(text){
  if(globalThis.navigator?.clipboard?.writeText){
    try{await navigator.clipboard.writeText(text);return}catch{/* Try browser copy in HTTP/permission-restricted deployments. */}
  }
  const previous=document.activeElement,selection=document.getSelection();
  const ranges=selection?Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange()):[];
  const field=document.createElement('textarea');field.value=text;field.className='clipboard-field';
  field.setAttribute('readonly','');document.body.append(field);field.select();
  try{if(!document.execCommand('copy'))throw new Error('Nie udało się skopiować. Zaznacz treść konsoli i skopiuj ręcznie.')}
  finally{
    field.remove();previous?.focus({preventScroll:true});
    if(selection){selection.removeAllRanges();ranges.forEach(range=>selection.addRange(range))}
  }
}
for(const button of document.querySelectorAll('[data-copy-console]')){
  const feedback=document.createElement('span');feedback.className='sr-only';feedback.setAttribute('role','status');button.after(feedback);
  button.addEventListener('click',async()=>{
    const console=document.getElementById(button.dataset.copyConsole);
    const text=Array.from(console.children,node=>node.textContent).join('\n');
    try{await copyConsoleText(text);button.title='Skopiowano';feedback.textContent='Skopiowano zawartość konsoli.'}
    catch(error){button.title=error.message;feedback.textContent=error.message}
  });
}
