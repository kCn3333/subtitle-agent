const archiveRows=document.querySelector('#archive-rows'),archiveStatus=document.querySelector('#archive-status');
function archiveCell(row,text){const cell=document.createElement('td');if(text!==undefined)cell.textContent=text;row.append(cell);return cell}
function archiveLinks(cell,files){
  if(!files.length){cell.textContent='—';return}
  const list=document.createElement('ul');list.className='archive-files';
  for(const file of files){
    const item=document.createElement('li'),link=document.createElement('a'),icon=document.createElement('i');
    icon.className='fa-solid fa-download';icon.setAttribute('aria-hidden','true');link.append(icon);
    const label=file.kind==='workpack'?'ZIP':file.kind==='translation'?'Tłumaczenie SRT':'Synchronizacja SRT';
    link.href=file.url;link.download=file.filename;link.append(document.createTextNode(` ${label} · ${file.sha256.slice(0,8)}`));
    link.title=`${file.filename}\nSHA-256: ${file.sha256}`;link.setAttribute('aria-label',`Pobierz ${file.filename}`);item.append(link);
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
      const cell=archiveCell(row),date=document.createElement('time'),name=document.createElement('span');
      date.className='archive-date';date.dateTime=title.date;date.textContent=new Date(title.date).toLocaleString('pl-PL');
      name.className='archive-title';name.textContent=title.title;cell.append(date,name);
      archiveLinks(archiveCell(row),title.workpacks);archiveLinks(archiveCell(row),title.subtitles);archiveRows.append(row);
    }
    archiveStatus.textContent=titles.length?`Ostatnie tytuły: ${titles.length} / 30`:'Archiwum jest puste.';
  }catch(error){archiveStatus.textContent=error.message}
}
loadArchive();
