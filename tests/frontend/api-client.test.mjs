// Optional, dependency-free checks: node --test tests/frontend/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {createClient} from '../../app/frontend/api.mjs';
import {escape} from '../../frontend/investigator/assets/ui.mjs';
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const session=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
const tokens={access_token:'test-access',refresh_token:'test-refresh'};

test('refreshes once and retries with rotated bearer, including /auth/me',async()=>{
  const calls=[];
  const storage=session();
  const api=createClient(async(path,options)=>{
    calls.push([path,options]);
    if(path.endsWith('/refresh')) return json({access_token:'rotated-access',refresh_token:'rotated-refresh'});
    return options.headers.Authorization==='Bearer rotated-access'?json({username:'officer'}):json({},401);
  },{storage});
  api.setTokens(tokens);
  assert.equal((await api.request('/auth/me')).username,'officer');
  assert.equal(calls.length,3);
  assert.equal(storage.getItem('saps_refresh_token'),'rotated-refresh');
  assert.equal(calls[0][1].cache,'no-store');
});

test('concurrent 401 responses share one refresh',async()=>{
  let refreshes=0;
  const api=createClient(async(path,options)=>{
    if(path.endsWith('/refresh')) {refreshes++;await new Promise(resolve=>setTimeout(resolve,10));return json({access_token:'new',refresh_token:'new-refresh'});}
    return options.headers.Authorization==='Bearer new'?json([]):json({},401);
  });
  api.setTokens(tokens);
  await Promise.all([api.request('/investigations/dockets'),api.request('/evidence/one')]);
  assert.equal(refreshes,1);
});

test('refresh failure clears session without a loop',async()=>{
  const storage=session();let calls=0,expired=0;
  const api=createClient(async()=>{calls++;return json({},401);},{storage,onExpired:()=>expired++});
  api.setTokens(tokens);
  await assert.rejects(api.request('/investigations/dockets'),{status:401});
  assert.equal(calls,2);assert.equal(expired,1);assert.equal(api.hasSession(),false);
});

test('a second 401 after successful refresh does not refresh again',async()=>{
  let calls=0;
  const api=createClient(async(path)=>{calls++;return path.endsWith('/refresh')?json(tokens):json({},401);});
  api.setTokens(tokens);
  await assert.rejects(api.request('/investigations/dockets'),{status:401});
  assert.equal(calls,3);assert.equal(api.hasSession(),false);
});

test('multipart leaves content type to browser and empty response is accepted',async()=>{
  const body=new FormData();body.append('file',new Blob(['evidence']),'evidence.txt');
  const api=createClient(async(_,options)=>{assert.equal(options.headers['Content-Type'],undefined);assert.equal(options.body,body);return new Response(null,{status:204});});
  assert.equal(await api.request('/evidence/item/files',{method:'POST',body}),null);
});

test('422 exposes field errors without echoing submitted content',async()=>{
  const api=createClient(async()=>json({detail:[{loc:['body','content'],msg:'Field required',input:'PRIVATE-NOTE'}]},422));
  await assert.rejects(api.request('/dockets/item/notes'),error=>error.status===422&&error.message==='content: Field required'&&!error.message.includes('PRIVATE-NOTE'));
});

test('403, 404 and 409 remain distinguishable and do not refresh',async()=>{
  for(const status of [403,404,409]) {
    let calls=0;const api=createClient(async()=>{calls++;return json({},status);});api.setTokens(tokens);
    await assert.rejects(api.request('/evidence/item'),{status});assert.equal(calls,1);
  }
});

test('logout clears tab session even when offline',async()=>{
  const api=createClient(async()=>{throw new Error('offline');},{storage:session()});api.setTokens(tokens);
  await assert.rejects(api.logout());assert.equal(api.hasSession(),false);
});

test('record text is escaped before rendering',()=>{
  assert.equal(escape('<script>"&\'</script>'),'&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;');
});
