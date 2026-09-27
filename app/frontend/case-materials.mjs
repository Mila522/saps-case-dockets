import {categoryChoices, bindCrimeCategory, complaintPayload, stationLabel, stationAddress} from './complaint-fields.mjs';
// Shared A/C dossier forms. The caller supplies its existing authenticated client.
import {escape as e, date} from '/investigator/assets/ui.mjs';
import {EVIDENCE_TYPES} from '/investigator/assets/contracts.mjs';

let sequence=0;
function input(prefix,name,label,{type='text',required=true,max=255,choices}={}) {
  const id=prefix+name;
  const attrs=`id="${id}" name="${name}" ${required?'required':''} maxlength="${max}"`;
  const control=choices?`<select ${attrs}>${choices.map(value=>`<option value="${e(Array.isArray(value)?value[0]:value)}">${e(Array.isArray(value)?value[1]:value.replaceAll('_',' '))}</option>`).join('')}</select>`:type==='textarea'?`<textarea ${attrs}></textarea>`:`<input ${attrs} type="${type}">`;
  return `<div class="field"><label for="${id}">${e(label)}</label>${control}</div>`;
}
function read(form) {
  const values={};
  for(const node of form.querySelectorAll('[name]')) {
    if(node.disabled) continue;
    const value=node.value.trim();
    if(node.required&&!value) {node.focus();throw new Error('Required fields cannot contain only spaces.');}
    if(value) values[node.name]=value;
  }
  return values;
}
function bind(form,action,message) {
  let busy=false;
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||!form.reportValidity())return;
    busy=true;const submit=form.querySelector('[type=submit]');submit.disabled=true;message.textContent='';
    try{await action(read(form));}catch(error){message.textContent=error.message;message.focus();}
    finally{busy=false;submit.disabled=false;}
  };
}
function message(parent) {
  const node=document.createElement('p');node.setAttribute('role','status');node.tabIndex=-1;parent.prepend(node);return node;
}
const statementFields=(prefix)=>input(prefix,'statement_text','Statement / correction (permanent new version)',{type:'textarea',max:20000});
const witnessFields=(prefix)=>input(prefix,'first_name','Witness first name',{max:100})+input(prefix,'last_name','Witness last name',{max:100})+
  input(prefix,'phone_number','Witness phone (optional)',{required:false,type:'tel',max:30})+input(prefix,'email','Witness email (optional)',{required:false,type:'email',max:254})+
  input(prefix,'address','Witness address (optional)',{required:false,max:4000})+input(prefix,'statement_text','Witness statement (optional)',{required:false,type:'textarea',max:20000});
const actionForm=(name,title,fields)=>`<details><summary>${e(title)}</summary><form data-action="${name}" class="form-stack">${fields}<button class="button button-primary" type="submit">Save record</button></form></details>`;

export async function mountMaterials(parent,complaintId,request,{initialEvidence=false}={}) {
  const prefix='material-'+(++sequence)+'-';
  parent.textContent='Loading statements and witnesses…';
  const report=message(parent);
  const path=`/complaints/${encodeURIComponent(complaintId)}`;
  let current;
  try{current=await request(path+'/materials');}catch(error){parent.textContent='';parent.append(report);report.textContent=error.message;return;}
  const versions=rows=>rows.length?rows.map(row=>`<article><strong>Version ${row.statement_version}${row.is_current?' · Current':''}</strong><small> · ${e(date(row.created_at))}${row.signed_at?' · Signed '+e(date(row.signed_at)):' · Signature not recorded'}</small><p class="statement-text">${e(row.statement_text)}</p></article>`).join(''):'<p>No statement recorded.</p>';
  parent.innerHTML=`<h3>Complainant statement</h3>${versions(current.statements)}<h3>Witnesses</h3>${current.witnesses.length?current.witnesses.map(w=>`<article><h4>${e(w.first_name)} ${e(w.last_name)}</h4><p>${e([w.phone_number,w.email,w.address].filter(Boolean).join(' · '))}</p>${versions(w.statements)}${current.can_append?actionForm('witness-'+w.id,'Append witness statement / correction',statementFields(prefix+w.id)):''}</article>`).join(''):'<p>No witnesses recorded. Add only actual witnesses; none are required merely to complete a form.</p>'}
    ${current.initial_evidence.length?`<h3>Initial evidence</h3>${current.initial_evidence.map(item=>`<p>${e(item.evidence_reference)} · ${e(item.title)} · ${e(item.current_storage_location)}</p>`).join('')}` : ''}
    ${current.can_append?actionForm('statement','Append complainant statement / correction',statementFields(prefix+'statement-'))+actionForm('witness','Record a witness',witnessFields(prefix+'witness-')):''}
    ${current.can_append&&initialEvidence?actionForm('evidence','Register initial evidence',input(prefix+'ev-','title','Evidence title')+input(prefix+'ev-','description','Evidence description',{type:'textarea',max:20000})+input(prefix+'ev-','evidence_type','Type',{choices:EVIDENCE_TYPES})+input(prefix+'ev-','format','Format',{choices:['PHYSICAL','DIGITAL']})+input(prefix+'ev-','storage_location','Current secure location')+input(prefix+'ev-','collection_location','Collection location (optional)',{required:false,max:4000})):''}`;
  const result=message(parent);
  const save=async(url,payload)=>{
    const saved=await request(url,{method:'POST',body:payload});
    await mountMaterials(parent,complaintId,request,{initialEvidence});
    const node=parent.querySelector('[role=status]');node.textContent=saved.evidence_reference?`Evidence registered: ${saved.evidence_reference}`:'Record saved as a permanent new entry.';node.focus();
  };
  for(const form of parent.querySelectorAll('form')) bind(form,async values=>{
    const kind=form.dataset.action;
    if(kind==='statement') return save(path+'/statements',{...values,expected_version:current.statements.at(-1)?.statement_version||0});
    if(kind==='witness')return save(path+'/witnesses',values);
    if(kind==='evidence'){values.is_digital=values.format==='DIGITAL';delete values.format;return save(path+'/initial-evidence',values);}
    const witness=current.witnesses.find(w=>kind==='witness-'+w.id);
    return save(`${path}/witnesses/${witness.id}/statements`,{...values,expected_version:witness.statements.at(-1)?.statement_version||0});
  },result);
}

export function mountWalkIn(parent,request,onCreated) {
  const p='walk-in-'+(++sequence)+'-';
  parent.innerHTML=`<p>For email updates and online tracking, help the complainant register and verify at the complainant portal first. Request their consent code below to link this complaint. Without consent, intake remains unlinked and no case emails are sent.</p><form class="form-stack">${input(p,'first_name','Complainant first name',{max:100})}${input(p,'last_name','Complainant last name',{max:100})}${input(p,'phone_number','Complainant phone',{type:'tel',max:30})}${input(p,'email','Complainant email (optional)',{type:'email',required:false,max:254})}${input(p,'address_line_1','Complainant residential address (optional)',{required:false})}${input(p,'city','Complainant city (optional)',{required:false,max:150})}${input(p,'province','Complainant province (optional)',{required:false,max:100})}<p><a href='/portal/' target='_blank' rel='noopener'>Open complainant registration / sign-in</a></p><button type='button' data-consent>Request account-link consent code</button>${input(p,'email_consent_code','Account-link consent code (optional)',{required:false,max:6})}<p>With consent, the supplied phone and residential address will be saved to the complainant account. The residential address is separate from the incident location.</p>${input(p,'crime_category','Crime category',{choices:categoryChoices})}${input(p,'incident_description','Complainant statement: what happened?',{type:'textarea',max:20000})}${input(p,'incident_location','Incident location')}${input(p,'incident_city','Incident city (optional)',{required:false,max:150})}${input(p,'incident_province','Incident province',{max:100})}<div class="field"><label for="${p}confirmed"><input id="${p}confirmed" type="checkbox" required> I have confirmed these details with the complainant.</label></div><p>Record actual witnesses after saving. Initial evidence can be registered after acceptance creates the docket. Missing witnesses or evidence must not be invented.</p><button class="button button-primary" type="submit">Register in-station complaint</button></form>`;
  const notice=message(parent);
  const form=parent.querySelector('form');
  const stationField=document.createElement('div');
  stationField.innerHTML=input(p,'station_id','Receiving station',{choices:[['','Loading your assigned station…']]})+`<p id="${p}station-address">Only your active assigned station is available.</p>`;
  form.prepend(stationField);
  const stationSelect=form.querySelector('[name=station_id]');
  const submit=form.querySelector('[type=submit]');
  stationSelect.disabled=true;submit.disabled=true;
  let ready=false,consentId=null,consentEmail=null;
  const consentButton=form.querySelector('[data-consent]');
  consentButton.onclick=async()=>{
    const email=form.querySelector('[name=email]').value.trim();
    if(!email){notice.textContent='Enter the verified account email first.';return;}
    consentButton.disabled=true;
    try {
      const response=await request('/complaints/in-station/email-consent',{method:'POST',body:{email}});
      consentId=response.challenge_id;consentEmail=email;
      notice.textContent=response.message;
    }catch(error){notice.textContent=error.message;}
    finally {setTimeout(()=>{consentButton.disabled=false;},60000);}
  };
  void request('/complaints/in-station/receiving-station').then(station=>{
    stationSelect.innerHTML=`<option value="${e(station.id)}">${e(stationLabel(station))}</option>`;
    stationField.querySelector('p').textContent=`Assigned receiving station: ${stationAddress(station)}`;
    stationSelect.disabled=false;submit.disabled=false;ready=true;
  }).catch(error=>{notice.textContent=error.message+' Close and reopen intake to retry.';});
  bindCrimeCategory(parent.querySelector('form'));
  bind(parent.querySelector('form'),async values=>{
    if(!ready) throw new Error('Your active receiving station must load before registration.');
    values = complaintPayload(values);
    const {first_name,last_name,phone_number,email,address_line_1,city,province,email_consent_code,...incident}=values;
    if(email_consent_code && (!consentId||consentEmail!==email)) throw new Error('Request a consent code for this email first.');
    if(email_consent_code) Object.assign(incident,{email_consent_id:consentId,email_consent_code});
    const result=await request('/complaints/in-station',{method:'POST',body:{...incident,complainant:{first_name,last_name,phone_number,...(email?{email}:{}),...(address_line_1?{address_line_1}:{}),...(city?{city}:{}),...(province?{province}:{})},details_confirmed_with_complainant:true}});
    await onCreated(result,Boolean(email_consent_code));
  },notice);
}
