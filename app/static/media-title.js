function readableMediaTitle(path){
  let stem=String(path||'').split(/[\\/]/).pop().replace(/\.[^.]+$/,'');
  const episode=/(?<![a-z0-9])s(\d{1,2})e(\d{1,3})(?:\s*[-._ ]?\s*e(\d{1,3}))?(?!\d)/i.exec(stem)||/(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?!\d)/i.exec(stem);
  const year=/(?<!\d)((?:18|19|20|21)\d{2})(?!\d)/.exec(stem);
  let suffix='';
  if(episode){stem=stem.slice(0,episode.index).replace(/(?<!\d)(?:18|19|20|21)\d{2}(?!\d)/g,'');suffix=` · S${episode[1].padStart(2,'0')}E${episode[2].padStart(2,'0')}${episode[3]?`–E${episode[3].padStart(2,'0')}`:''}`}
  else if(year){stem=stem.slice(0,year.index);suffix=` (${year[1]})`}
  stem=stem.replace(/[. _\-\[(]+(?:2160p|1080p|720p|bluray|blu-ray|web[-_. ]?dl|webrip|hdtv|x26[45]|h[. ]?26[45]).*$/i,'');
  stem=stem.replace(/[._\[\](){}]+/g,' ').replace(/\s+/g,' ').replace(/^[ -]+|[ -]+$/g,'');
  return (stem||'Nieznany tytuł')+suffix;
}
function operationName(mode){return ({INSPECT:'Sprawdzanie napisów',PREPARE_SYNC:'Przygotowanie do synchronizacji',PREPARE_TRANSLATION:'Przygotowanie do tłumaczenia'})[mode]||'Przygotowanie napisów'}
