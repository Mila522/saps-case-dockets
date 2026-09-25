import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/frontend/app.mjs', import.meta.url), 'utf8');
const functionSource = source.slice(source.indexOf('async function newComplaint()'), source.indexOf("document.querySelector('#login-tab')"));
function harness(request) {
  const nodes = [], fields = [], buttons = [];
  const select = {disabled: false, children: [], replaceChildren() { this.children = []; }};
  const submit = {disabled: false, dataset: {}};
  const text = (value, tag='p', parent) => {
    const node = {textContent: value, tag, dataset: {}, setAttribute() {}};
    nodes.push(node); parent?.children?.push(node); return node;
  };
  const context = vm.createContext({api:{request}, page(){}, text,
    form(items) { fields.push(...items); return {querySelector: s => s === '[name=station_id]' ? select : submit}; },
    button(label, action) { const b={label, action}; buttons.push(b); return b; }
  });
  vm.runInContext(functionSource, context);
  return {start:()=>context.newComplaint(), nodes, fields, buttons, select, submit};
}
test('fields exist before station loading completes; success enables station selection', async () => {
  let resolve;
  const h=harness(()=>new Promise(r=>{resolve=r;}));
  const loading=h.start();
  assert(h.fields.some(([name])=>name==='incident_description'));
  assert.equal(h.submit.dataset.unavailable, 'true');
  resolve([{id:'station-1',name:'Demo',province:'Test'}]);
  await loading;
  assert.equal(h.select.disabled,false);
  assert.equal(h.submit.dataset.unavailable,'false');
  assert.equal(h.select.children[1].value,'station-1');
});
test('failed load retains form and offers retry; empty list keeps submission blocked', async () => {
  let fail=true;
  const h=harness(async()=>{if(fail) throw new Error('Unavailable'); return [];});
  await assert.rejects(h.start(), /Unavailable/);
  assert(h.fields.some(([name])=>name==='incident_location'));
  assert.equal(h.buttons[0].hidden,false);
  fail=false;
  await h.buttons[0].action();
  assert.equal(h.select.disabled,true);
  assert.equal(h.submit.dataset.unavailable,'true');
  assert(h.nodes.some(n=>n.textContent.includes('No active receiving stations')));
});
