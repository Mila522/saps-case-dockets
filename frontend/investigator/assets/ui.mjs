export const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const label = value => String(value ?? '').toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
export const date = value => value ? new Intl.DateTimeFormat('en-ZA', {dateStyle:'medium',timeStyle:'short',timeZone:'Africa/Johannesburg'}).format(new Date(value)) : 'Not recorded';
export const badge = value => `<span class="status status-${escape(String(value).toLowerCase().replaceAll('_','-'))}">${escape(label(value))}</span>`;
export const link = (text, href) => `<a class="button button-secondary" href="${escape(href)}">${escape(text)}</a>`;
export const empty = text => `<p class="empty">${escape(text)}</p>`;
export const options = (values, blank = '') => (blank ? `<option value="">${escape(blank)}</option>` : '') + values.map(value => {
  const [id, text] = Array.isArray(value) ? value : [value,label(value)];
  return `<option value="${escape(id)}">${escape(text)}</option>`;
}).join('');
export function field(name, text, {type='text', choices, required=true, max, hint='', wide=false, value=''} = {}) {
  const attrs = `id="${name}" name="${name}" ${required ? 'required' : ''} ${max ? `maxlength="${max}"` : ''} aria-describedby="${name}-hint ${name}-error"`;
  const input = choices ? `<select ${attrs}>${options(choices)}</select>` : type === 'textarea' ? `<textarea ${attrs}></textarea>` : `<input ${attrs} type="${type}" value="${escape(value)}">`;
  return `<div class="field ${wide ? 'wide' : ''}"><label for="${name}">${escape(text)}</label>${input}<small class="field-hint" id="${name}-hint">${escape(hint)}</small><p class="field-error" id="${name}-error"></p></div>`;
}
export function check(name, text) { return `<div class="check-field"><input type="checkbox" id="${name}" name="${name}"><label for="${name}">${escape(text)}</label></div>`; }
export function facts(rows) { return `<dl class="record-grid">${rows.map(([name,value,wide]) => `<div class="${wide ? 'wide' : ''}"><dt>${escape(name)}</dt><dd>${escape(value ?? 'Not recorded')}</dd></div>`).join('')}</dl>`; }
export function table(headers, rows) {
  return `<table class="data-table"><thead><tr>${headers.map(h=>`<th scope="col">${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((value,i)=>`<td data-label="${escape(headers[i])}">${value}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
export function section(id, title, body) { return `<section class="panel" id="${id}" aria-labelledby="${id}-heading"><h2 id="${id}-heading">${escape(title)}</h2>${body}</section>`; }
export function form(id, title, fields, submit) { return `<details class="panel"><summary>${escape(title)}</summary><form id="${id}"><div class="form-grid">${fields}</div><div class="actions"><button class="button button-primary" type="submit">${escape(submit)}</button><button class="button button-secondary" type="reset">Reset form</button></div></form></details>`; }
export function notice(text, success=false) {
  const node = document.querySelector('#notice');
  node.textContent = text; node.hidden = !text; node.classList.toggle('success',success);
  if (text) node.focus();
}
export function fieldErrors(form, errors=[]) {
  for (const input of form.querySelectorAll('[name]')) {
    input.removeAttribute('aria-invalid');
    const target = document.getElementById(input.name+'-error'); if (target) target.textContent='';
  }
  for (const error of errors) {
    const input = form.elements.namedItem(error.loc?.at(-1));
    if (!input) continue;
    input.setAttribute('aria-invalid','true');
    const target = document.getElementById(input.name+'-error'); if (target) target.textContent=error.msg;
  }
}
export function data(form) {
  const result = {};
  for (const input of form.querySelectorAll('[name]')) {
    if (input.disabled) continue;
    if (input.type === 'checkbox') { result[input.name]=input.checked; continue; }
    if (input.type === 'file') continue;
    const value = input.value.trim();
    if (input.required && !value) {
      fieldErrors(form,[{loc:[input.name],msg:'Enter more than whitespace.'}]);
      input.focus(); throw new Error('Complete the highlighted field.');
    }
    if (value) result[input.name] = input.type === 'datetime-local' ? new Date(value).toISOString() : value;
  }
  return result;
}
export function confirmAction(title, copy) {
  const dialog=document.querySelector('#confirmation');
  document.querySelector('#confirmation-title').textContent=title;
  document.querySelector('#confirmation-copy').textContent=copy;
  dialog.returnValue='cancel'; dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true}));
}
