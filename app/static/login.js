const loginForm=document.querySelector('#login-form'),loginStatus=document.querySelector('#login-status');
loginForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=loginForm.querySelector('button');button.disabled=true;loginStatus.textContent='Logowanie…';
  try{
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json',
      'X-CSRF-Token':document.querySelector('meta[name="login-csrf-token"]').content},
      body:JSON.stringify({username:document.querySelector('#username').value,password:document.querySelector('#password').value})});
    const body=await response.json();
    if(response.ok){window.location.replace('/');return}
    document.querySelector('#password').value='';
    const retry=Number(response.headers.get('Retry-After'));
    loginStatus.textContent=(body.detail?.message||'Logowanie nie powiodło się.')+
      (response.status===429&&retry>0?` Spróbuj ponownie za ${Math.ceil(retry/60)} min.`:'');
  }catch{loginStatus.textContent='Nie udało się połączyć. Spróbuj ponownie.'}
  finally{button.disabled=false}
});
