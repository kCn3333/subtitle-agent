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
function archiveCost(cell,cost){
  cell.className='archive-cost';
  if(!cost){cell.textContent='—';cell.title='Brak zapisanych metryk AI';return}
  const usd=document.createElement('span'),tokens=document.createElement('span');
  usd.className='archive-cost-usd';tokens.className='archive-cost-tokens';
  const amount=cost.usd===null?'—':String(cost.usd).replace('.',',');
  usd.textContent=`${cost.usd_partial&&cost.usd!==null?'≥ ':''}${amount} USD`;
  tokens.textContent=`${cost.tokens_partial&&cost.total_tokens!==null?'≥ ':''}${cost.total_tokens===null?'—':cost.total_tokens.toLocaleString('pl-PL')} tokenów`;
  cell.title=`Zapisane żądania AI: ${cost.requests}. ${cost.usd_partial||cost.tokens_partial?'Suma niepełna: API nie podało wszystkich metryk w USD lub tokenach. ':''}Suma obejmuje dostępne zapisy; starsze usunięte metryki nie są odtwarzane.`;
  cell.append(usd,tokens);
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
      archiveLinks(archiveCell(row),title.workpacks);archiveLinks(archiveCell(row),title.subtitles);archiveCost(archiveCell(row),title.cost);archiveRows.append(row);
    }
    archiveStatus.textContent=titles.length?'':'Archiwum jest puste.';
    archiveStatus.hidden=!!titles.length;
  }catch(error){archiveStatus.hidden=false;archiveStatus.textContent=error.message}
}
loadArchive();
