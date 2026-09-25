import {createClient} from '/portal/api.mjs';
import {escape, link, notice, fieldErrors, data} from './ui.mjs';
import {queue} from './investigator-dockets.mjs';
import {docketDetail} from './docket-detail.mjs';
import {evidenceDetail} from './evidence-detail.mjs';

const view=document.querySelector('#view');
let busy=false;
let user=null;
let expired=false;
const api=createClient(fetch,{storage:sessionStorage,onExpired:()=>{expired=true; signIn('Your session expired. Sign in again.');}});
function signIn(message='Sign in with your investigating officer account to see your assignments.') {
  user=null;
  document.querySelector('#identity').textContent='Authorised personnel';
  document.querySelector('#logout').hidden=true;
  view.setAttribute('aria-busy','false');
  view.innerHTML=`<p class="eyebrow">Investigator workspace</p><h1>Your next step in the investigation.</h1><p class="intro">${escape(message)}</p><div class="panel"><h2>Sign in to your workspace</h2><p>Use the existing staff sign-in with password and email verification.</p>${link('Sign in securely','/officer/?workspace=investigator')}</div>`;
}
async function all(path) {
  const rows=[];
  for (let offset=0;;offset+=100) {
    const batch=await api.request(`${path}${path.includes('?')?'&':'?'}limit=100&offset=${offset}`);
    rows.push(...batch);
    if(batch.length<100) return rows;
  }
}
function render(html,title) {
  if (expired) return;
  view.innerHTML=html; view.setAttribute('aria-busy','false');
  document.title=`${title} · Investigator workspace`;
}
async function run(task, form=null) {
  if(busy) return;
  busy=true; notice('');
  // Navigation is also held during writes, preventing a second page from resubmitting.
  const buttons=[...view.querySelectorAll('button')];
  const disabled=buttons.map(button=>button.disabled);
  buttons.forEach(button=>button.disabled=true);
  try { await task(); }
  catch(error) {
    if(error.status===401) signIn('Your session expired. Sign in again.');
    else if(error.status===403 || error.status===404) render(`<h1>Record unavailable</h1><p>${escape(error.message)}</p>${link('Assigned dockets','/investigator/')}`,'Record unavailable');
    else if(error.status===409) {
      try { await load(); } catch(refreshError) { render(`<h1>Unable to refresh</h1><p>${escape(refreshError.message)}</p>${link('Assigned dockets','/investigator/')}`,'Unable to refresh'); }
    }
    else if(view.getAttribute('aria-busy')==='true') {
      render(`<h1>Unable to load casework</h1><p>${escape(error.message)}</p><button class="button button-secondary" id="retry">Try again</button>`,'Unable to load');
      document.querySelector('#retry').onclick=()=>run(user?load:initialise);
    }
    if(form?.isConnected) fieldErrors(form,error.fields);
    notice(error.message);
  } finally { busy=false; buttons.forEach((button,i)=>button.disabled=disabled[i]); view.setAttribute('aria-busy','false'); }
}
function bindForm(id, action) {
  const form=document.getElementById(id);
  if(!form) return;
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(form.reportValidity()) run(async()=>{fieldErrors(form); await action(data(form),form);},form);
  });
  form.addEventListener('reset',()=>fieldErrors(form));
}
const ctx={api,all,render,run,bindForm,load,has:code=>user?.permissions.some(p=>p.code===code),get user(){return user;}};
async function load() {
  render('<h1>Loading casework…</h1><p>Please wait while your access is checked.</p>','Loading');
  view.setAttribute('aria-busy','true');
  const id=new URLSearchParams(location.search).get('id');
  if(location.pathname !== '/investigator/' && !/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(id||'')) throw new Error('Open a record from the assigned docket queue. The record link is invalid.');
  if(location.pathname==='/investigator/docket') await docketDetail(ctx,id);
  else if(location.pathname==='/investigator/evidence') await evidenceDetail(ctx,id);
  else await queue(ctx);
}
document.addEventListener('click',event=>{if(busy && event.target.closest('a')) event.preventDefault();});
document.querySelector('#logout').onclick=async()=>{
  if(busy) return;
  busy=true;
  const loggingOut=api.logout();
  expired=true; signIn('You have signed out.');
  try {await loggingOut;notice('You have signed out.',true);}catch(error){notice(error.message);}finally{busy=false;}
};
window.addEventListener('pageshow',event=>{if(event.persisted) location.reload();});
async function initialise() {
  user=await api.request('/auth/me');
  document.querySelector('#identity').textContent=`${user.username} · ${user.roles.map(role=>role.name).join(', ')}`;
  document.querySelector('#logout').hidden=false;
  if(!user.roles.some(role=>role.code==='INVESTIGATING_OFFICER')) throw Object.assign(new Error('Permission denied. An investigating officer account is required.'),{status:403});
  await load();
}
if(!api.hasSession()) signIn();
else run(initialise);
