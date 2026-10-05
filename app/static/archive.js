const archiveRows=document.querySelector('#archive-rows'),archiveStatus=document.querySelector('#archive-status');
function archiveCell(row,text){const cell=document.createElement('td');if(text!==undefined)cell.textContent=text;row.append(cell);return cell}
function archiveLinks(cell,files){
  if(!files.length){cell.textContent='—';return}
  const list=document.createElement('ul');list.className='archive-files';
  for(const file of files){
    const item=document.createElement('li'),link=document.createElement('a'),icon=document.createElement('i');
    icon.className='fa-solid fa-download';icon.setAttribute('aria-hidden','true');link.append(icon);
    link.href=file.url;link.append(document.createTextNode(` ${file.filename}`));
    link.title=`SHA-256: ${file.sha256}`;item.append(link);
    if(file.kind!=='workpack'){const kind=document.createElement('small');kind.textContent=file.kind==='translation'?'Tłumaczenie':'Synchronizacja';item.append(kind)}
    list.append(item);
  }
  cell.append(list);
}
async function loadArchive(){
  try{
    const response=await fetch('/api/archive');if(!response.ok)throw new Error('Nie udało się wczytać archiwum');
    const {titles}=await response.json();archiveRows.replaceChildren();
    for(const title of titles){
      const row=document.createElement('tr');
      archiveCell(row,new Date(title.date).toLocaleString('pl-PL'));
      archiveCell(row,title.title);archiveLinks(archiveCell(row),title.workpacks);archiveLinks(archiveCell(row),title.subtitles);archiveRows.append(row);
    }
    archiveStatus.textContent=titles.length?`Ostatnie tytuły: ${titles.length} / 30`:'Archiwum jest puste.';
  }catch(error){archiveStatus.textContent=error.message}
}
loadArchive();
