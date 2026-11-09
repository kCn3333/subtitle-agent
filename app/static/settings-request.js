async function settingsRequest(url,options={}){
  const response=await fetch(url,options),body=await response.json();
  if(!response.ok)throw new Error(body.detail?.message||(Array.isArray(body.detail)?body.detail.map(item=>item.msg).join('; '):'Nie udało się wykonać żądania'));
  return body;
}
