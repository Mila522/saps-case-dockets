import {createClient} from '/portal/api.mjs';
import {escape as e} from '/investigator/assets/ui.mjs';
const $=selector=>document.querySelector(selector);
const api=createClient(fetch,{storage:sessionStorage,onExpired:()=>location.replace('/officer/')});
let options,rows=[],usersOffset=0,auditOffset=0;
const message=text=>{const n=$('#notice');n.textContent=text;n.hidden=!text;};
async function run(fn){try{await fn();}catch(error){message(error.message);}}
function query(form,offset){const params=new URLSearchParams({limit:25,offset});for(const [key,value] of new FormData(form)){if(value)params.set(key,['start','end'].includes(key)?new Date(value).toISOString():value);}return params;}
const rolesOptions=(staff=false)=>options.roles.filter(r=>!staff||options.staff_roles.includes(r.code)).map(r=>`<option value="${e(r.code)}">${e(r.name)}</option>`).join('');
const stationsOptions=()=>options.stations.map(s=>`<option value="${s.id}">${e(s.name)}${s.is_active?'':' (inactive)'}</option>`).join('');
async function users(){
 const page=await api.request('/admin/users?'+query($('#user-filters'),usersOffset));rows=page.items;
 $('#user-count').textContent=`${page.total} accounts · page ${Math.floor(usersOffset/25)+1}`;
 $('#users-prev').disabled=usersOffset===0;$('#users-next').disabled=usersOffset+25>=page.total;
 $('#user-list').innerHTML=`<table><thead><tr><th>Account</th><th>Role / station</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${e(r.username)}<br>${e(r.email)}<br><small>${r.id}</small></td><td>${e(r.roles.join(', '))}<br>${e(r.station_name||'No station')}</td><td>${r.is_active?'Active':'Inactive'}<br>${r.is_verified?'Email verified':'Email verification required'}</td><td>${r.editable?`<button class="button" data-edit="${r.id}">Manage staff</button>`:'Read only'}</td></tr>`).join('')}</tbody></table>`;
 $('#user-list').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(rows.find(r=>r.id===b.dataset.edit)));
}
function edit(row=null){
 const editor=$('#editor');editor.hidden=false;
 editor.innerHTML=`<h2>${row?'Manage '+e(row.username):'Provision staff account'}</h2><p>${row?'Role, station and activation changes end existing sessions. Reassign open dockets before transferring an investigator.':'Use a strong initial password agreed securely with the staff member. It is never emailed or returned. First sign-in requires the emailed verification code.'}</p><form id="staff-form"><div class="form-grid">
 ${!row?'<label>Username<input name="username" required minlength="3" maxlength="100" autocomplete="off"></label><label>Email<input name="email" type="email" required maxlength="254" autocomplete="off"></label><label>Initial password<input name="password" type="password" required minlength="12" maxlength="1024" autocomplete="new-password"></label>':''}
 <label>Staff role<select name="role" required><option value="">Select role</option>${rolesOptions(true)}</select></label><label>Station<select name="station_id" required><option value="">Select active station</option>${stationsOptions()}</select></label><label>Service number<input name="service_number" required maxlength="50"></label><label>Rank<input name="rank" required maxlength="100"></label><label>Phone (optional)<input name="phone_number" maxlength="30"></label>
 ${row?'<label>Account status<select name="is_active"><option value="true">Active</option><option value="false">Inactive</option></select></label>':''}</div><div class="actions"><button type="submit" class="button button-primary">${row?'Save account':'Create staff account'}</button><button id="cancel-edit" type="button" class="button">Cancel</button></div></form>`;
 const form=$('#staff-form');
 for(const s of options.stations.filter(s=>!s.is_active && s.id!==row?.station_id)){form.elements.station_id.querySelector(`option[value="${s.id}"]`).disabled=true;}
 if(row){for(const k of ['station_id','service_number','rank','phone_number','is_active'])form.elements[k].value=row[k]??'';form.elements.role.value=row.roles[0];}
 $('#cancel-edit').onclick=()=>{editor.replaceChildren();editor.hidden=true;};
 form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;message('');
   const body=Object.fromEntries(new FormData(form));body.phone_number=body.phone_number||null;
   if(row){body.is_active=body.is_active==='true';body.expected_updated_at=row.updated_at;}
   try{await api.request('/admin/users'+(row?'/'+row.id:''),{method:row?'PATCH':'POST',body});editor.replaceChildren();editor.hidden=true;message(row?'Staff account updated. Existing sessions revoked.':'Staff account created. Email verification remains required.');await users();}
   catch(error){message(error.message);button.disabled=false;}
   finally{if(form.elements.password)form.elements.password.value='';delete body.password;}
 };editor.scrollIntoView({block:'start',behavior:'smooth'});
}
async function audits(){const page=await api.request('/admin/audit?'+query($('#audit-filters'),auditOffset));
 $('#audit-count').textContent=`${page.total} entries · page ${Math.floor(auditOffset/25)+1}`;$('#audit-prev').disabled=auditOffset===0;$('#audit-next').disabled=auditOffset+25>=page.total;
 $('#audit-list').innerHTML=`<table><thead><tr><th>Time</th><th>Actor</th><th>Action / record</th><th>Details</th></tr></thead><tbody>${page.items.map(r=>`<tr><td>${e(new Date(r.occurred_at).toLocaleString())}</td><td>${e(r.actor_user_id||r.actor_type)}</td><td>${e(r.action)}<br>${e(r.entity_type)} ${e(r.entity_id||'')}</td><td><button class="button" data-audit="${r.id}">Open entry</button></td></tr>`).join('')}</tbody></table>`;
 $('#audit-list').querySelectorAll('[data-audit]').forEach(b=>b.onclick=()=>run(async()=>{const entry=await api.request('/admin/audit/'+b.dataset.audit);const section=$('#audit-detail');section.hidden=false;section.innerHTML='<h3>Recorded audit entry</h3><pre></pre>';section.querySelector('pre').textContent=JSON.stringify(entry,null,2);section.scrollIntoView({block:'start'});}));
}
$('#users-tab').onclick=()=>{$('#users-panel').hidden=false;$('#audit-panel').hidden=true;};
$('#audit-tab').onclick=()=>{$('#users-panel').hidden=true;$('#audit-panel').hidden=false;run(audits);};
$('#user-filters').onsubmit=event=>{event.preventDefault();usersOffset=0;run(users);};
$('#audit-filters').onsubmit=event=>{event.preventDefault();auditOffset=0;run(audits);};
for(const [name,load] of [['users',users],['audit',audits]])for(const [direction,delta] of [['prev',-25],['next',25]])$('#'+name+'-'+direction).onclick=()=>{if(name==='users')usersOffset=Math.max(0,usersOffset+delta);else auditOffset=Math.max(0,auditOffset+delta);run(load);};
$('#create-staff').onclick=()=>edit();
$('#logout').onclick=()=>run(async()=>{await api.logout();location.replace('/officer/');});
await run(async()=>{const me=await api.request('/auth/me');if(!me.roles.some(r=>r.code==='SYSTEM_ADMINISTRATOR')){message('System Administrator access required.');$('#identity').textContent='Access denied';return;}
 $('#identity').textContent=me.username;$('#logout').hidden=false;options=await api.request('/admin/options');
 $('#user-filters [name=role]').innerHTML='<option value="">All roles</option>'+rolesOptions();$('#user-filters [name=station_id]').innerHTML='<option value="">All stations</option>'+stationsOptions();
 $('#workspace').hidden=false;await users();});
