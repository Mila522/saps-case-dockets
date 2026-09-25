import {mountWalkIn, mountMaterials} from '/portal/case-materials.mjs';
const API = '/api/v1';
const dossierRequest=(path,options={})=>request(path,{...options,...(options.body?{body:JSON.stringify(options.body)}:{})});
if (new URLSearchParams(location.search).get('workspace') === 'investigator') {
  document.documentElement.classList.add('investigator-signin');
  document.title = 'Investigator sign-in · Case Desk prototype';
  document.querySelector('.security-note').textContent = 'Prototype · Not an official SAPS product · Password + email verification';
}

const state = {
  accessToken: sessionStorage.getItem('saps_access_token'),
  refreshToken: sessionStorage.getItem('saps_refresh_token'),
  user: null,
  challengeToken: null,
  authChallenge: null,
  resendReadyAt: 0,
  authBusy: false,
  resendUnavailable: false,
  complaints: [],
  reasons: [],
  escalations: [],
  dockets: [],
  investigators: [],
  activeComplaint: null,
  activeEscalation: null,
  activeDocket: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[character]));
}

function label(value = '') {
  return String(value).toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function formatDate(value) {
  if (!value) return 'Not recorded';
  return new Intl.DateTimeFormat('en-ZA', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Africa/Johannesburg',
  }).format(new Date(value));
}

function statusBadge(value) {
  const className = String(value).toLowerCase().replaceAll('_', '-');
  return `<span class="status status-${className}">${escapeHtml(label(value))}</span>`;
}

function showMessage(element, text, success = false) {
  element.textContent = text;
  element.classList.toggle('is-success', success);
  element.hidden = false;
}

function clearMessage(element) {
  element.hidden = true;
  element.textContent = '';
  element.classList.remove('is-success');
}

function globalMessage(text, success = false) {
  const element = $('#global-message');
  showMessage(element, text, success);
  window.clearTimeout(globalMessage.timeout);
  globalMessage.timeout = window.setTimeout(() => clearMessage(element), 7000);
}

function errorText(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload.detail === 'string') return payload.detail;
  if (Array.isArray(payload.detail)) return payload.detail.map(item => item.msg || 'Invalid value').join('. ');
  return fallback;
}

async function request(path, options = {}, retry = true) {
  const headers = new Headers(options.headers || {});
  if (state.accessToken) headers.set('Authorization', `Bearer ${state.accessToken}`);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (response.status === 401 && retry && state.refreshToken && (!path.startsWith('/auth/') || path === '/auth/me')) {
    const refreshed = await refreshSession();
    if (refreshed) return request(path, options, false);
  }
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch { payload = null; }
    const error = new Error(errorText(payload, `Request failed (${response.status})`));
    error.status = response.status;
    error.retryAfter = Math.max(0, Number(response.headers.get('Retry-After')) || 0);
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function refreshSession() {
  try {
    const response = await fetch(`${API}/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    });
    if (!response.ok) throw new Error('Session expired');
    setTokens(await response.json());
    return true;
  } catch {
    clearSession();
    showAuth();
    showMessage($('#auth-message'), 'Your session expired. Sign in again.');
    return false;
  }
}

function setTokens(tokens) {
  state.accessToken = tokens.access_token;
  state.refreshToken = tokens.refresh_token;
  sessionStorage.setItem('saps_access_token', state.accessToken);
  sessionStorage.setItem('saps_refresh_token', state.refreshToken);
}

function clearSession() {
  state.accessToken = null;
  state.refreshToken = null;
  state.user = null;
  sessionStorage.removeItem('saps_access_token');
  sessionStorage.removeItem('saps_refresh_token');
}

function setAuthStep(step) {
  if (step === 'login') { state.challengeToken = null; state.authChallenge = null; state.resendUnavailable = false; }
  $('#login-form').hidden = step !== 'login';
  $('#mfa-form').hidden = step !== 'mfa';
  clearMessage($('#auth-message'));
  const focusTarget = step === 'login' ? '#identifier' : '#mfa-code';
  window.setTimeout(() => $(focusTarget)?.focus(), 0);
}

function showAuth() {
  $('#auth-view').hidden = false;
  $('#app-view').hidden = true;
  setAuthStep('login');
}

async function enterApplication() {
  state.user = await request('/auth/me');
  const roles = state.user.roles.map(role => role.code);
  const permissions = new Set(state.user.permissions.map(permission => permission.code));
  $('#intake-open').hidden = !roles.includes('CHARGE_OFFICER') || !permissions.has('complaint.register');
  // Reuse this login/MFA flow for C; the destination is fixed, never a supplied URL.
  if (new URLSearchParams(location.search).get('workspace') === 'investigator') {
    if (roles.includes('INVESTIGATING_OFFICER') && permissions.has('docket.view_assigned')) {
      location.replace('/investigator/');
      return;
    }
    clearSession();
    showAuth();
    showMessage($('#auth-message'), 'An investigating officer account is required for this workspace.');
    return;
  }
  const allowedRole = roles.some(role => ['CHARGE_OFFICER', 'STATION_COMMANDER', 'NCC_OFFICER'].includes(role));
  if (!allowedRole) {
    clearSession();
    showAuth();
    showMessage($('#auth-message'), 'This workspace is limited to charge officers, station commanders and NCC escalation officers.');
    return;
  }
  $('#auth-view').hidden = true;
  $('#app-view').hidden = false;
  $('#user-name').textContent = state.user.username;
  $('#user-role').textContent = roles.map(label).join(' · ');

  const access = {
    complaints: permissions.has('complaint.view_station'),
    escalations: permissions.has('refusal.escalation.view'),
    dockets: permissions.has('docket.approve'),
  };
  $$('.nav-item').forEach(button => { button.hidden = !access[button.dataset.view]; });
  const firstView = Object.keys(access).find(view => access[view]);
  if (firstView) switchView(firstView);
  await Promise.all([
    access.complaints ? loadComplaints() : Promise.resolve(),
    permissions.has('complaint.decide') ? loadReasons() : Promise.resolve(),
    access.escalations ? loadEscalations() : Promise.resolve(),
    access.dockets ? loadDockets() : Promise.resolve(),
    permissions.has('docket.assign') ? loadInvestigators() : Promise.resolve(),
  ]);
}

async function handleLogin(event) {
  event.preventDefault();
  const button = $('button[type="submit"]', event.currentTarget);
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = 'Signing in…';
  clearMessage($('#auth-message'));
  try {
    const result = await request('/auth/login', {
      method: 'POST', body: JSON.stringify({
        username: $('#identifier').value.trim(), password: $('#password').value,
      }),
    });
    $('#password').value = '';
    showChallenge(result);
  } catch (error) {
    showMessage($('#auth-message'), error.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Continue securely';
  }
}

function showChallenge(result) {
  state.authChallenge = result;
  state.challengeToken = result.challenge_token;
  state.resendReadyAt = Date.now() + result.resend_after * 1000;
  state.resendUnavailable = false;
  setAuthStep('mfa');
  $('#mfa-code').value = '';
  const transition = result.status === 'TOTP_TRANSITION_REQUIRED';
  $('#email-guidance').textContent = transition
    ? 'Verify your existing authenticator once to authorize switching to email. If it is unavailable, contact your account administrator for identity recovery.'
    : `Code submitted to the mail server for ${result.masked_recipient}. Check inbox or spam; expires in five minutes. Inbox delivery is not confirmed.`;
  $('#resend-code').hidden = transition;
  updateVerificationButtons();
}
function updateVerificationButtons() {
  const left = Math.max(0, Math.ceil((state.resendReadyAt - Date.now()) / 1000));
  $$('#mfa-form button').forEach(button => button.disabled = state.authBusy);
  $('#resend-code').disabled = state.authBusy || left > 0 || state.resendUnavailable;
  $('#resend-code').textContent = state.authBusy === 'resend' ? 'Sending code…' : state.resendUnavailable ? 'Return to sign-in to retry' : left ? `Resend code (${left}s)` : 'Resend code';
  $('#mfa-form button[type=submit]').textContent = state.authBusy === 'verify' ? 'Verifying…' : 'Verify and sign in';
}
setInterval(updateVerificationButtons, 500);
async function handleMfa(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  if (state.authBusy) return;
  state.authBusy = 'verify';
  updateVerificationButtons();
  clearMessage($('#auth-message'));
  try {
    const transition = state.authChallenge.status === 'TOTP_TRANSITION_REQUIRED';
    const result = await request(transition ? '/auth/email/transition' : '/auth/email/verify', {
      method:'POST', body:JSON.stringify({challenge_token:state.challengeToken,code:$('#mfa-code').value})
    });
    if (transition) { showChallenge(result); return; }
    setTokens(result);
    form.reset();
    state.challengeToken = null;
    state.authChallenge = null;
    await enterApplication();
  } catch(error) { showMessage($('#auth-message'), error.message); }
  finally { state.authBusy = false; updateVerificationButtons(); }
}
$('#resend-code').addEventListener('click', async () => {
  if (state.authBusy || state.resendUnavailable || !state.challengeToken || Date.now() < state.resendReadyAt) return;
  state.authBusy = 'resend'; updateVerificationButtons(); clearMessage($('#auth-message'));
  try {
    showChallenge(await request('/auth/email/resend', {method:'POST',body:JSON.stringify({challenge_token:state.challengeToken})}));
    showMessage($('#auth-message'), 'A new code was submitted. Check your inbox or spam folder and use the newest code.', true);
  } catch(error) {
    if (error.retryAfter) state.resendReadyAt = Date.now() + error.retryAfter * 1000;
    if (error.status === 401 || error.status === 503) state.resendUnavailable = true;
    showMessage($('#auth-message'), error.message);
  } finally { state.authBusy = false; updateVerificationButtons(); }
});

async function logout() {
  const refreshToken = state.refreshToken;
  clearSession();
  showAuth();
  if (refreshToken) {
    try { await request('/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }) }, false); } catch { /* local logout still succeeds */ }
  }
}

function switchView(view) {
  $$('.view').forEach(section => {
    const active = section.id === `${view}-view`;
    section.hidden = !active;
    section.classList.toggle('is-active', active);
  });
  $$('.nav-item').forEach(button => button.classList.toggle('is-active', button.dataset.view === view));
  $('#main-content').focus();
}

async function loadComplaints() {
  try {
    const page = await request('/complaints/station?limit=100');
    state.complaints = page.items;
    renderComplaints();
  } catch (error) {
    globalMessage(error.message);
  }
}

function renderComplaints() {
  const filter = $('#complaint-status').value;
  const rows = state.complaints.filter(row => !filter || row.status === filter);
  $('#complaint-result-count').textContent = `${rows.length} complaint${rows.length === 1 ? '' : 's'} shown`;
  const counts = Object.fromEntries(['SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'ESCALATED'].map(status => [status, state.complaints.filter(row => row.status === status).length]));
  $('#metric-submitted').textContent = counts.SUBMITTED;
  $('#metric-review').textContent = counts.UNDER_REVIEW;
  $('#metric-accepted').textContent = counts.ACCEPTED + state.complaints.filter(row=>row.status==='DOCKET_CREATED').length;
  $('#metric-escalated').textContent = counts.ESCALATED;
  $('#complaint-empty').hidden = rows.length !== 0;
  $('#complaint-table').innerHTML = rows.map(row => {
    const action = row.status === 'SUBMITTED'
      ? `<button class="button button-primary button-small" data-complaint-action="start" data-id="${row.id}">Start review</button>`
      : row.status === 'UNDER_REVIEW'
        ? `<button class="button button-primary button-small" data-complaint-action="decide" data-id="${row.id}">Record decision</button>`
        : row.status === 'ACCEPTED'
          ? `<button class="button button-gold button-small" data-complaint-action="docket" data-id="${row.id}">Create docket</button>`
          : `<button class="button button-secondary button-small" data-complaint-action="view" data-id="${row.id}">View</button>`;
    return `<tr>
      <td><span class="reference">${escapeHtml(row.reference_number)}</span><span class="subtext">${escapeHtml(row.channel)}</span></td>
      <td>${escapeHtml(row.crime_category)}<span class="subtext">${escapeHtml(row.incident_city || row.incident_province)}</span></td>
      <td>${escapeHtml(formatDate(row.submitted_at))}</td>
      <td>${statusBadge(row.status)}</td>
      <td><div class="actions">${action}<button class="button button-secondary button-small" data-complaint-action="materials" data-id="${row.id}">Statements and witnesses</button></div></td>
    </tr>`;
  }).join('');
}

async function loadReasons() {
  try {
    state.reasons = await request('/refusal-reasons');
    $('#refusal-reason').innerHTML = '<option value="">Select a controlled reason</option>' + state.reasons.map(reason =>
      `<option value="${reason.id}">${escapeHtml(reason.name)}</option>`).join('');
  } catch (error) {
    globalMessage(error.message);
  }
}

function complaintSummary(row) {
  return `<div class="summary-item"><span>Reference</span><strong>${escapeHtml(row.reference_number)}</strong></div>
    <div class="summary-item"><span>CAS number</span><strong>${escapeHtml(row.cas_number || 'Not allocated')}</strong></div>
    <div class="summary-item"><span>Status</span><strong>${escapeHtml(label(row.status))}</strong></div>
    <div class="summary-item"><span>Category</span><strong>${escapeHtml(row.crime_category)}</strong></div>
    <div class="summary-item"><span>Incident date</span><strong>${escapeHtml(formatDate(row.incident_occurred_at))}</strong></div>
    <div class="summary-item summary-item-wide"><span>Location</span><strong>${escapeHtml(row.incident_location)}, ${escapeHtml(row.incident_city || row.incident_province)}</strong></div>
    <div class="summary-item summary-item-wide"><span>Description</span><strong>${escapeHtml(row.incident_description)}</strong></div>`;
}

function openComplaintDialog(row, readOnly = false) {
  state.activeComplaint = row;
  $('#complaint-summary').innerHTML = complaintSummary(row);
  $('#complaint-dialog-title').textContent = readOnly ? 'Complaint details' : 'Record complaint decision';
  $('#decision-choice').hidden = readOnly;
  $('#refusal-fields').hidden = true;
  $('#complaint-submit').hidden = readOnly;
  clearMessage($('#complaint-dialog-message'));
  $('#complaint-action-form').reset();
  $('#complaint-dialog').showModal();
}

async function complaintAction(button) {
  const row = state.complaints.find(item => item.id === button.dataset.id);
  if (!row) return;
  button.disabled = true;
  try {
    if (button.dataset.complaintAction === 'start') {
      const updated = await request(`/complaints/${row.id}/review`, { method: 'POST' });
      globalMessage(`${updated.reference_number} is now under review.`, true);
      await loadComplaints();
      openComplaintDialog(updated);
    } else if (button.dataset.complaintAction === 'decide') {
      openComplaintDialog(row);
    } else if (button.dataset.complaintAction === 'view') {
      openComplaintDialog(row, true);
    } else if (button.dataset.complaintAction === 'materials') {
      $('#materials-dialog').showModal();
      await mountMaterials($('#materials-content'),row.id,dossierRequest,{initialEvidence:Boolean(row.docket_id)});
    } else if (button.dataset.complaintAction === 'docket') {
      if (!window.confirm(`Ensure the legacy accepted complaint ${row.reference_number} has a docket?`)) return;
      const docket = await request(`/complaints/${row.id}/dockets`, { method: 'POST' });
      globalMessage(`Docket ${docket.cas_number} is available.`, true);
      await loadComplaints();
    }
  } catch (error) {
    globalMessage(error.message);
  } finally {
    button.disabled = false;
  }
}

function updateRefusalFields() {
  const decision = $('input[name="decision"]:checked')?.value;
  $('#refusal-fields').hidden = decision !== 'REFUSED';
  $('#refusal-reason').required = decision === 'REFUSED';
}

function updateReasonRequirement() {
  const reason = state.reasons.find(item => item.id === $('#refusal-reason').value);
  const required = Boolean(reason?.requires_officer_notes);
  $('#officer-notes').required = required;
  $('#notes-required').textContent = required ? '(required)' : '(optional)';
}

async function submitDecision(event) {
  event.preventDefault();
  if (!state.activeComplaint) return;
  const decision = $('input[name="decision"]:checked')?.value;
  if (!decision) return showMessage($('#complaint-dialog-message'), 'Select accept or refuse.');
  const payload = { decision };
  if (decision === 'REFUSED') {
    payload.refusal_reason_id = $('#refusal-reason').value;
    payload.officer_notes = $('#officer-notes').value.trim() || null;
  }
  const button = $('#complaint-submit');
  if (button.disabled) return;
  button.disabled = true;
  clearMessage($('#complaint-dialog-message'));
  try {
    const result = await request(`/complaints/${state.activeComplaint.id}/decisions`, {
      method: 'POST', body: JSON.stringify(payload),
    });
    $('#complaint-dialog').close();
    globalMessage(result.cas_number ? `Accepted. Docket ${result.cas_number} created and awaiting commander approval.` : `Decision recorded. Complaint is now ${label(result.complaint_status)}.`, true);
    await loadComplaints();
  } catch (error) {
    showMessage($('#complaint-dialog-message'), error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadEscalations() {
  try {
    state.escalations = await request('/refusal-escalations');
    renderEscalations();
  } catch (error) {
    globalMessage(error.message);
  }
}

function renderEscalations() {
  const filter = $('#escalation-status').value;
  const rows = state.escalations.filter(row => !filter || row.status === filter);
  const openCount = state.escalations.filter(row => row.status !== 'RESOLVED').length;
  $('#escalation-count').textContent = openCount;
  $('#escalation-count').hidden = openCount === 0;
  $('#escalation-result-count').textContent = `${rows.length} escalation${rows.length === 1 ? '' : 's'} shown`;
  $('#escalation-empty').hidden = rows.length !== 0;
  $('#escalation-list').innerHTML = rows.map(row => {
    const actions = row.status === 'OPEN'
      ? `<button class="button button-secondary button-small" data-escalation-action="acknowledge" data-id="${row.id}">Acknowledge</button><button class="button button-primary button-small" data-escalation-action="resolve" data-id="${row.id}">Resolve</button>`
      : row.status === 'ACKNOWLEDGED'
        ? `<button class="button button-primary button-small" data-escalation-action="resolve" data-id="${row.id}">Resolve</button>` : '';
    return `<article class="escalation-card">
      <div><h3>Complaint ${escapeHtml(row.complaint_id)}</h3><div class="escalation-meta"><span>${statusBadge(row.status)}</span><span>${escapeHtml(label(row.target))}</span><span>Escalated ${escapeHtml(formatDate(row.escalated_at))}</span></div>${row.resolution_notes ? `<p>${escapeHtml(row.resolution_notes)}</p>` : ''}</div>
      <div class="actions">${actions}</div>
    </article>`;
  }).join('');
}

async function escalationAction(button) {
  const row = state.escalations.find(item => item.id === button.dataset.id);
  if (!row) return;
  if (button.dataset.escalationAction === 'resolve') {
    state.activeEscalation = row;
    $('#resolve-form').reset();
    clearMessage($('#resolve-message'));
    $('#resolve-dialog').showModal();
    return;
  }
  button.disabled = true;
  try {
    await request(`/refusal-escalations/${row.id}/acknowledge`, { method: 'POST' });
    globalMessage('Escalation acknowledged.', true);
    await loadEscalations();
  } catch (error) {
    globalMessage(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitResolution(event) {
  event.preventDefault();
  const button = $('button[type="submit"]', event.currentTarget);
  button.disabled = true;
  clearMessage($('#resolve-message'));
  try {
    await request(`/refusal-escalations/${state.activeEscalation.id}/resolve`, {
      method: 'POST', body: JSON.stringify({ resolution_notes: $('#resolution-notes').value.trim() }),
    });
    $('#resolve-dialog').close();
    globalMessage('Escalation resolved.', true);
    await loadEscalations();
  } catch (error) {
    showMessage($('#resolve-message'), error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadDockets() {
  try {
    state.dockets = await request('/dockets?limit=100');
    renderDockets();
  } catch (error) {
    globalMessage(error.message);
  }
}

function renderDockets() {
  const filter = $('#docket-status').value;
  const rows = state.dockets.filter(row => !filter || row.status === filter);
  $('#docket-result-count').textContent = `${rows.length} docket${rows.length === 1 ? '' : 's'} shown`;
  $('#docket-empty').hidden = rows.length !== 0;
  $('#docket-table').innerHTML = rows.map(row => {
    const actions = row.status === 'PENDING_APPROVAL'
      ? `<button class="button button-primary button-small" data-docket-action="review" data-id="${row.id}">Review</button>`
      : row.status === 'APPROVED'
        ? `<button class="button button-gold button-small" data-docket-action="assign" data-id="${row.id}">Assign investigator</button>`
        : `<button class="button button-secondary button-small" data-docket-action="view" data-id="${row.id}">View</button>`;
    return `<tr><td><span class="reference">${escapeHtml(row.cas_number)}</span></td><td><span class="reference">${escapeHtml(row.complaint_id)}</span></td><td>${escapeHtml(formatDate(row.opened_at))}</td><td>${statusBadge(row.status)}</td><td><div class="actions">${actions}<button class="button button-secondary button-small" data-docket-action="materials" data-id="${row.id}">Statements and witnesses</button></div></td></tr>`;
  }).join('');
}

function docketSummary(row) {
  return `<div class="summary-item"><span>CAS number</span><strong>${escapeHtml(row.cas_number)}</strong></div><div class="summary-item"><span>Status</span><strong>${escapeHtml(label(row.status))}</strong></div><div class="summary-item summary-item-wide"><span>Complaint</span><strong>${escapeHtml(row.complaint_id)}</strong></div><div class="summary-item"><span>Opened</span><strong>${escapeHtml(formatDate(row.opened_at))}</strong></div>`;
}

async function docketAction(button) {
  const row = state.dockets.find(item => item.id === button.dataset.id);
  if (!row) return;
  state.activeDocket = row;
  if (button.dataset.docketAction === 'materials') {
    $('#materials-dialog').showModal();
    await mountMaterials($('#materials-content'),row.complaint_id,dossierRequest);
    return;
  }
  if (button.dataset.docketAction === 'assign') {
    $('#assignment-form').reset();
    $('#assignment-summary').innerHTML = docketSummary(row);
    clearMessage($('#assignment-message'));
    $('#assignment-dialog').showModal();
  } else {
    $('#docket-action-form').reset();
    $('#docket-summary').innerHTML = docketSummary(row);
    $('#docket-dialog-title').textContent = button.dataset.docketAction === 'review' ? 'Review docket' : 'Docket details';
    $$('.choice-group, #docket-notes-field, #docket-action-form .dialog-actions .button-primary', $('#docket-dialog')).forEach(element => { element.hidden = button.dataset.docketAction === 'view'; });
    clearMessage($('#docket-dialog-message'));
    $('#docket-dialog').showModal();
  }
}

function updateDocketNotes() {
  const decision = $('input[name="docket-decision"]:checked')?.value;
  const required = decision === 'RETURNED_FOR_CORRECTION';
  $('#docket-notes').required = required;
  $('#docket-notes-required').textContent = required ? '(required)' : '(optional)';
}

async function submitDocketDecision(event) {
  event.preventDefault();
  const decision = $('input[name="docket-decision"]:checked')?.value;
  if (!decision) return showMessage($('#docket-dialog-message'), 'Select approve or return for correction.');
  const button = $('button[type="submit"]', event.currentTarget);
  button.disabled = true;
  clearMessage($('#docket-dialog-message'));
  try {
    await request(`/dockets/${state.activeDocket.id}/approvals`, {
      method: 'POST', body: JSON.stringify({ decision, notes: $('#docket-notes').value.trim() || null }),
    });
    $('#docket-dialog').close();
    globalMessage(`Docket decision recorded: ${label(decision)}.`, true);
    await loadDockets();
  } catch (error) {
    showMessage($('#docket-dialog-message'), error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadInvestigators() {
  try {
    state.investigators = await request('/stations/investigators');
    $('#investigator').innerHTML = '<option value="">Select an active station investigator</option>' + state.investigators.map(officer =>
      `<option value="${officer.id}">${escapeHtml(officer.rank)} · ${escapeHtml(officer.service_number)} (${escapeHtml(officer.username)})</option>`).join('');
  } catch (error) {
    globalMessage(error.message);
  }
}

async function submitAssignment(event) {
  event.preventDefault();
  const button = $('button[type="submit"]', event.currentTarget);
  button.disabled = true;
  clearMessage($('#assignment-message'));
  try {
    await request(`/dockets/${state.activeDocket.id}/assignments`, {
      method: 'POST', body: JSON.stringify({
        investigating_officer_id: $('#investigator').value,
        reason: $('#assignment-reason').value.trim(),
      }),
    });
    $('#assignment-dialog').close();
    globalMessage(`Investigator assigned to ${state.activeDocket.cas_number}.`, true);
    await loadDockets();
  } catch (error) {
    showMessage($('#assignment-message'), error.message);
  } finally {
    button.disabled = false;
  }
}

function bindEvents() {
  $('#intake-open').onclick=()=>{
    mountWalkIn($('#intake-content'),dossierRequest,async row=>{
      $('#intake-dialog').close();
      await loadComplaints();
      globalMessage(`In-station complaint registered: ${row.reference_number}`,true);
    });
    $('#intake-dialog').showModal();
  };
  $('#login-form').addEventListener('submit', handleLogin);
  $('#mfa-form').addEventListener('submit', event => handleMfa(event, false));
  $$('.back-auth').forEach(button => button.addEventListener('click', () => setAuthStep('login')));
  $('#logout-button').addEventListener('click', logout);
  $$('.nav-item').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
  $$('.refresh-button').forEach(button => button.addEventListener('click', () => ({ complaints: loadComplaints, escalations: loadEscalations, dockets: loadDockets }[button.dataset.refresh])()));
  $('#complaint-status').addEventListener('change', renderComplaints);
  $('#escalation-status').addEventListener('change', renderEscalations);
  $('#docket-status').addEventListener('change', renderDockets);
  $('#complaint-table').addEventListener('click', event => event.target.closest('[data-complaint-action]') && complaintAction(event.target.closest('[data-complaint-action]')));
  $('#escalation-list').addEventListener('click', event => event.target.closest('[data-escalation-action]') && escalationAction(event.target.closest('[data-escalation-action]')));
  $('#docket-table').addEventListener('click', event => event.target.closest('[data-docket-action]') && docketAction(event.target.closest('[data-docket-action]')));
  $$('input[name="decision"]').forEach(input => input.addEventListener('change', updateRefusalFields));
  $('#refusal-reason').addEventListener('change', updateReasonRequirement);
  $$('input[name="docket-decision"]').forEach(input => input.addEventListener('change', updateDocketNotes));
  $('#complaint-action-form').addEventListener('submit', submitDecision);
  $('#resolve-form').addEventListener('submit', submitResolution);
  $('#docket-action-form').addEventListener('submit', submitDocketDecision);
  $('#assignment-form').addEventListener('submit', submitAssignment);
  $$('.dialog-close').forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    button.closest('dialog').close();
  }));
}

async function initialise() {
  bindEvents();
  if (!state.accessToken) return showAuth();
  try {
    await enterApplication();
  } catch {
    const refreshed = await refreshSession();
    if (refreshed) await enterApplication();
  }
}

initialise();
