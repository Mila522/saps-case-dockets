import {mountSubmissionExtras, mountUploads, downloadPrivate} from './submission-extras.mjs';
import {categoryChoices, bindCrimeCategory, complaintPayload, stationLabel, stationAddress} from './complaint-fields.mjs';
import {createClient} from './api.mjs';
import {mountEmailStatus} from './case-email-ui.mjs';
import {complaintFromHash} from './case-link.mjs';
let linkedComplaint = complaintFromHash(window.location.hash);
const api = createClient();
const view = document.querySelector('#view');
const message = document.querySelector('#message');
let busy = false;
const post = (path, body) => api.request(path, {method: 'POST', body});
function note(text = '') { message.textContent = text; }
function session(active) {
  for (const id of ['mine-tab', 'new-tab', 'track-tab', 'logout']) document.getElementById(id).hidden = !active;
  for (const id of ['login-tab', 'register-tab']) document.getElementById(id).hidden = active;
}
async function run(task) {
  if (busy) return;
  busy = true; note();
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await task(); }
  catch (error) {
    if (error.status === 401) { api.clear(); session(false); login(); }
    note(error.message);
  } finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = b.dataset.unavailable === 'true' || Number(b.dataset.readyAt || 0) > Date.now()); }
}
function page(title) { view.replaceChildren(); const heading = document.createElement('h2'); heading.textContent = title; view.append(heading); }
function text(value, tag = 'p', parent = view) { const node = document.createElement(tag); node.textContent = value; parent.append(node); return node; }
function button(label, action, parent = view) { const node = text(label, 'button', parent); node.type = 'button'; node.onclick = () => run(action); return node; }
function form(fields, submitLabel, action) {
  const node = document.createElement('form');
  for (const [name, label, options = {}] of fields) {
    const id = 'field-' + name; const caption = text(label, 'label', node); caption.htmlFor = id;
    const input = document.createElement(options.tag || 'input'); input.id = id; input.name = name;
    input.required = !options.optional;
    for (const [key, value] of Object.entries(options)) if (!['tag','optional','hint','choices'].includes(key)) input.setAttribute(key, value);
    if (options.choices) for (const [value, label] of options.choices) { const option = text(label, 'option', input); option.value = value; }
    node.append(input); if (options.hint) { const hint = text(options.hint, 'small', node); hint.id = id + '-hint'; input.setAttribute('aria-describedby', hint.id); }
  }
  const submit = text(submitLabel, 'button', node); submit.type = 'submit';
  node.onsubmit = event => { event.preventDefault(); if (node.reportValidity()) run(() => action(Object.fromEntries(new FormData(node)), node)); };
  view.append(node); return node;
}
function login() {
  page('Sign in');
  form([['username','Username or email',{autocomplete:'username',maxlength:254}],['password','Password',{type:'password',autocomplete:'current-password',maxlength:1024}]], 'Continue', async data => mfa(await post('/auth/login', data)));
  button('Forgot password?', forgotPassword);
}
function register() {
  page('Create your account');
  form([['first_name','First name',{maxlength:100,autocomplete:'given-name'}],['last_name','Last name',{maxlength:100,autocomplete:'family-name'}],
    ['username','Username',{minlength:3,maxlength:100,pattern:'[A-Za-z0-9][A-Za-z0-9_.\\-]*',autocomplete:'username'}],['email','Email',{type:'email',maxlength:254,autocomplete:'email'}],
    ['address_line_1','Residential address',{maxlength:255,autocomplete:'street-address'}],['city','City',{maxlength:150,autocomplete:'address-level2'}],['province','Province',{maxlength:100,autocomplete:'address-level1'}],
    ['phone_number','Phone number',{type:'tel',maxlength:30,autocomplete:'tel'}],['password','Password',{type:'password',minlength:12,maxlength:1024,autocomplete:'new-password',hint:'Use at least 12 characters, uppercase and lowercase letters, a number and a special character. No surrounding spaces.'}]],
    'Create account', async data => {
      if (data.password !== data.password.trim() || !/[A-Z]/.test(data.password) || !/[a-z]/.test(data.password) || !/\d/.test(data.password) || !/[^\w\s]/.test(data.password)) throw new Error('Your password must meet all the requirements shown below the field.');
      await mfa(await post('/auth/register', data));
    });
}
async function signedIn(tokens) {
  api.setTokens(tokens); session(true);
  if(linkedComplaint){const id=linkedComplaint;linkedComplaint=null;window.history.replaceState(null,'',window.location.pathname);await detail(id);}
  else await mine();
}
function forgotPassword() {
  page('Reset your password');
  form([['email','Account email',{type:'email',maxlength:254}]],'Send recovery instructions',async data=>{
    const result=await post('/auth/forgot-password',data);note(result.message);
  });
  text('Already have a recovery token? Paste it below. It expires after 15 minutes.');
  form([['token','Recovery token',{maxlength:128,autocomplete:'off'}],['password','New password',{type:'password',minlength:12,maxlength:1024,autocomplete:'new-password',hint:'Use uppercase and lowercase letters, a number and a special character.'}]],'Change password',async data=>{
    const result=await post('/auth/reset-password',data);api.clear();session(false);login();note(result.message);
  });
  button('Back to sign-in',login);
}
async function mfa(challenge) {
  if(challenge.access_token)return signedIn(challenge);
  page('Verify your account');
  const transition = challenge.status === 'TOTP_TRANSITION_REQUIRED';
  text(transition ? 'To protect your existing account, verify your current authenticator once before switching to email. If you no longer have it, contact your account administrator for identity recovery.' : `A code was submitted to the mail server for ${challenge.masked_recipient}. Check your inbox or spam folder. It expires in five minutes; inbox delivery is not confirmed.`);
  const verification = form([['code',transition ? 'Existing account verification code' : 'Six-digit email code',{inputmode:'numeric',pattern:'[0-9]{6}',minlength:6,maxlength:6,autocomplete:'one-time-code'}]], 'Verify and sign in', async data => {
    const submit = verification.querySelector('[type=submit]');
    submit.textContent = 'Verifying…';
    try {
      const result = await post(transition ? '/auth/email/transition' : '/auth/email/verify', {challenge_token:challenge.challenge_token,code:data.code});
      if (transition) return mfa(result);
      api.setTokens(result); session(true); page('Signed in');
      if (linkedComplaint) {
        const id = linkedComplaint; linkedComplaint = null;
        window.history.replaceState(null, '', window.location.pathname);
        await detail(id); // The normal endpoint enforces the verified user's ownership.
      } else await mine();
    } catch(error) { note(error.message); } // Preserve the code form and challenge on failure.
    finally { submit.textContent = 'Verify and sign in'; }
  });
  const actions = document.createElement('div'); actions.className = 'verification-actions'; view.append(actions);
  if (!transition) {
    let readyAt = Date.now() + challenge.resend_after * 1000;
    const resend = button('Resend code', async () => {
      if (Date.now() < readyAt) { note('Please wait for the resend cooldown.'); return; }
      resend.textContent = 'Sending code…';
      resend.dataset.sending = 'true';
      try {
        await mfa(await post('/auth/email/resend', {challenge_token:challenge.challenge_token}));
        note('A new code was submitted. Check your inbox or spam folder and use the newest code.');
      } catch(error) {
        if (error.retryAfter) { readyAt = Date.now() + error.retryAfter * 1000; resend.dataset.readyAt = readyAt; }
        if (error.status === 401 || error.status === 503) resend.dataset.unavailable = 'true';
        note(error.message);
      } finally { resend.dataset.sending = 'false'; }
    }, actions);
    resend.id = 'resend-code'; resend.dataset.readyAt = readyAt;
    const tick = () => {
      if (!resend.isConnected) return;
      const left = Math.max(0, Math.ceil((readyAt-Date.now())/1000));
      resend.disabled = left > 0 || busy || resend.dataset.unavailable === 'true';
      resend.textContent = resend.dataset.sending === 'true' ? 'Sending code…' : resend.dataset.unavailable === 'true' ? 'Return to sign-in to retry' : left ? `Resend code (${left}s)` : 'Resend code';
      setTimeout(tick, 250);
    }; tick();
  }
  button('Back to sign-in', login, actions).className = 'back-signin';
}
function trackReference() {
  page('Track reference');
  form([['reference_number','Track one of your complaint references',{maxlength:100}]], 'Track reference', async data=>{
    const item=await post('/complaints/track-by-reference',{reference_number:data.reference_number.trim()});
    await detail(item.id);
  });
  document.querySelector('#field-reference_number').focus();
}
async function mine(offset = 0) {
  page('My complaints'); text('Loading complaints…');
  const data = await api.request(`/complaints/mine?limit=10&offset=${offset}`);
  page('My complaints');
  const emailPanel=document.createElement('section');view.append(emailPanel);
  void mountEmailStatus(emailPanel,(path,options)=>api.request(path,options));
  if (!data.items.length) text('No complaints to show. Submit a new complaint to get started.');
  for (const item of data.items) {
    const card = document.createElement('article'); card.className = 'card'; view.append(card);
    text(item.reference_number, 'h3', card); text(item.status.replaceAll('_',' '), 'p', card);
    button('View complaint', () => detail(item.id), card);
  }
  const pager = document.createElement('div'); pager.className = 'pager'; view.append(pager);
  if (offset > 0) button('Previous', () => mine(Math.max(0, offset - 10)), pager);
  if (data.has_more) button('Next', () => mine(offset + 10), pager);
  button('Refresh complaints', () => mine(offset));
}
async function detail(id) {
  // Clear previous private content before every request, including denied requests.
  page('Complaint details'); text('Loading complaint…');
  const item = await api.request(`/complaints/${encodeURIComponent(id)}/tracking`);
  page('Complaint details'); const list = document.createElement('dl'); view.append(list);
  for (const [label, value] of [['Reference',item.reference_number],['CAS number',item.cas_number || 'Not allocated'],['Status',item.status.replaceAll('_',' ')],['Submitted',date(item.submitted_at)],['Review started',date(item.review_started_at)],['Last updated',date(item.updated_at)]]) { text(label,'dt',list); text(value,'dd',list); }
  button('Refresh status', () => detail(id)); button('Back to my complaints', () => mine());
  text('Download confirmations','h3');
  const confirmation = type => async()=>{
    const doc=await post(`/complaints/${id}/documents`,{document_type:type});
    await downloadPrivate((p,o)=>api.request(p,o),`/documents/${doc.id}/download`,`${doc.document_number}.html`);
  };
  button('Download registration confirmation',confirmation('COMPLAINT_REGISTRATION_CONFIRMATION'));
  if(['REFUSED','ESCALATED'].includes(item.status))button('Download refusal confirmation',confirmation('REFUSAL_CONFIRMATION'));
  const files=document.createElement('section');view.append(files);
  await mountUploads(files,id,(p,o)=>api.request(p,o));
  if(!['REFUSED','ESCALATED'].includes(item.status)){
    const uploadForm=form([['file','Add evidence (image, video, document or voice recording)',{type:'file',accept:'image/*,video/*,audio/*,.pdf,.doc,.docx,.txt,.odt,.csv'}]],'Upload evidence',async(data,node)=>{
      const body=new FormData(node);body.append('complaint_id',id);await api.request('/complaints/uploads',{method:'POST',body});await detail(id);note('Evidence uploaded.');
    });
  }
  const updates = await api.request(`/complaints/${encodeURIComponent(id)}/updates`);
  if (updates.docket_status) text('Case status: ' + updates.docket_status.replaceAll('_',' '));
  text('Official updates and appointments', 'h3');
  for (const update of updates.feedback) {
    const article=document.createElement('article'); view.append(article);
    text(update.subject,'h4',article);text(update.message,'p',article);text(date(update.published_at),'small',article);
  }
  if (!updates.feedback.length) text('No official updates published yet.');
}
function date(value) { return value ? new Date(value).toLocaleString() : 'Not started'; }
async function newComplaint() {
  page('Submit a complaint');
  const stationStatus = text('Loading receiving stations…');
  stationStatus.setAttribute('role', 'status');
  const complaintForm = form([['station_id','Receiving station',{tag:'select',choices:[['','Select a receiving station']]}],
    ['crime_category','Crime category',{tag:'select',choices:categoryChoices}],['incident_description','Your statement: what happened?',{tag:'textarea',maxlength:20000,hint:'Your description is preserved as the first statement. Add optional witnesses and evidence below.'}],
    ['incident_location','Incident location',{maxlength:255}],['incident_city','City (optional)',{optional:true,maxlength:150}],
    ['incident_province','Province',{maxlength:100}],['incident_occurred_at','Incident date and time (optional)',{type:'datetime-local',optional:true,hint:'Enter the time in your device’s local timezone.'}]], 'Submit complaint', async (data, node) => {
      data = complaintPayload(data);
      if (stationSelect.disabled || !stationSelect.value) throw new Error('Select a receiving station before submitting.');
      for (const key of Object.keys(data)) data[key] = data[key].trim();
      for (const key of ['crime_category','incident_description','incident_location','incident_province']) if (!data[key]) throw new Error('Complete all required fields with more than spaces.');
      if (data.incident_occurred_at) { const occurred = new Date(data.incident_occurred_at); if (occurred > new Date()) throw new Error('The incident date cannot be in the future.'); data.incident_occurred_at = occurred.toISOString(); } else delete data.incident_occurred_at;
      if (!data.incident_city) delete data.incident_city;
      Object.assign(data,await collectExtras());
      const result = await post('/complaints', data); node.reset();
      page('Complaint submitted'); text('Keep your reference number:'); text(result.reference_number,'code');
      text('Status: ' + result.status.replaceAll('_',' ')); button('Track this complaint', () => detail(result.id)); button('My complaints', () => mine());
    });
  const collectExtras=mountSubmissionExtras(complaintForm,(p,o)=>api.request(p,o));
  bindCrimeCategory(complaintForm);
  const stationSelect = complaintForm.querySelector('[name=station_id]');
  const stationDetails = document.createElement('p'); stationDetails.className = 'station-address'; stationDetails.setAttribute('role','status'); stationDetails.hidden = true; stationSelect.after(stationDetails);
  let loadedStations = [];
  stationSelect.addEventListener('change', () => {
    const station = loadedStations.find(item=>item.id===stationSelect.value);
    stationDetails.hidden = !station;
    stationDetails.textContent = station ? 'Receiving station address: ' + (stationAddress(station) || 'Address not recorded') : '';
  });
  const submit = complaintForm.querySelector('[type=submit]');
  const retry = button('Retry loading stations', loadStations);
  async function loadStations() {
    stationSelect.disabled = true;
    submit.disabled = true;
    submit.dataset.unavailable = 'true';
    retry.hidden = true;
    stationStatus.textContent = 'Loading receiving stations…';
    try {
      const stations = await api.request('/complaints/receiving-stations');
      loadedStations = stations;
      stationDetails.hidden = true;
      stationSelect.replaceChildren();
      const placeholder = text('Select a receiving station', 'option', stationSelect);
      placeholder.value = '';
      for (const station of stations) {
        const option = text(stationLabel(station), 'option', stationSelect);
        option.value = station.id;
      }
      stationSelect.disabled = !stations.length;
      submit.dataset.unavailable = String(!stations.length);
      stationStatus.textContent = stations.length ? 'Choose the station that should receive your complaint.' : 'No active receiving stations are configured. Contact the project administrator. You can fill in the details below, but cannot submit yet.';
      retry.hidden = !!stations.length;
    } catch (error) {
      stationStatus.textContent = 'Receiving stations could not be loaded. Your entered details are kept; use Retry loading stations.';
      retry.hidden = false;
      throw error;
    }
  }
  await loadStations();
}
document.querySelector('#login-tab').onclick = () => run(async () => login());
document.querySelector('#register-tab').onclick = () => run(async () => register());
document.querySelector('#mine-tab').onclick = () => run(() => mine());
document.querySelector('#track-tab').onclick = () => run(async () => trackReference());
document.querySelector('#new-tab').onclick = () => run(newComplaint);
document.querySelector('#logout').onclick = () => run(async () => { try { await api.logout(); note('You have signed out.'); } finally { session(false); login(); } });
session(false);
if (!linkedComplaint && new URLSearchParams(window.location.search).get('page') === 'register') register();
else login();
