import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {mountEmailStatus} from '../../app/frontend/case-email-ui.mjs';

function node(){return {children:[],value:'',hidden:false,disabled:false,textContent:'',
  replaceChildren(...items){this.children=items;},append(item){this.children.push(item);},
  setAttribute(){},reportValidity(){return true;},querySelector(){return this.button??=(node());}};}

test('email status states unverified addresses are unsent and renders server states as text',async()=>{
  const root=node();const previous=globalThis.document;
  globalThis.document={createElement:node};
  try{
    let path;
    await mountEmailStatus(root,async p=>{path=p;return {email_verified:false,deliveries:[
      {event_type:'complaint.registered',status:'FAILED',reason:'EMAIL_NOT_VERIFIED'}]};},'case-id');
    assert.equal(path,'/case-email/complaints/case-id/status');
    assert.match(root.children[1].textContent,/not sent/);
    assert.match(root.children[2].textContent,/FAILED/);
    assert.equal(root.children.some(child=>child.tagName==='FORM'),false);
  }finally{globalThis.document=previous;}
});

test('case links select only a UUID and cannot authorize or redirect', async()=>{
  const {complaintFromHash}=await import('../../app/frontend/case-link.mjs');
  const id='12345678-1234-4321-abcd-123456789abc';
  assert.equal(complaintFromHash('#complaint='+id),id);
  for(const hash of ['#next=https://evil.example','#complaint='+id+'&token=secret','#complaint=../auth/me','']){
    assert.equal(complaintFromHash(hash),null);
  }
});

test('linked case is requested only after normal MFA verification succeeds',async()=>{
  const source=readFileSync(new URL('../../app/frontend/app.mjs',import.meta.url),'utf8');
  const functionSource=source.slice(source.indexOf('async function mfa('),source.indexOf('async function mine('));
  const calls=[];let submit;
  const context=vm.createContext({linkedComplaint:'12345678-1234-4321-abcd-123456789abc',
    page(){},text(){},note(){},session(){},login(){},mine(){calls.push('mine');},detail(id){calls.push(['detail',id]);},
    api:{setTokens(){calls.push('tokens');}},post:async(path)=>{calls.push(path);return {};},
    form(fields,label,action){submit=action;return {querySelector:()=>node()};},
    button(){return {...node(),dataset:{}};},view:node(),document:{createElement:node},
    window:{history:{replaceState(){}},location:{pathname:'/portal/'}},Date,
  });
  vm.runInContext(functionSource,context);
  await context.mfa({status:'EMAIL_CODE_REQUIRED',challenge_token:'test',masked_recipient:'test',resend_after:60});
  assert.equal(calls.length,0);
  await submit({code:'123456'});
  assert.deepEqual(calls,['/auth/email/verify','tokens',['detail','12345678-1234-4321-abcd-123456789abc']]);
});

test('staff failed resend clears its revoked challenge and returns to sign-in',async()=>{
  const source=readFileSync(new URL('../../frontend/officer/assets/app.js',import.meta.url),'utf8');
  const step=source.slice(source.indexOf('function setAuthStep('),source.indexOf('function showAuth('));
  const resend=source.slice(source.indexOf("$('#resend-code').addEventListener"),source.indexOf('async function logout('));
  for(const status of [503,401,429]){
    let handler;
    const nodes=Object.fromEntries(['resend-code','mfa-code','mfa-form','login-form','auth-message'].map(id=>['#'+id,node()]));
    nodes['#resend-code'].addEventListener=(_,fn)=>handler=fn;
    nodes['#mfa-code'].value='123456';
    const state={authBusy:false,resendUnavailable:false,challengeToken:'old',authChallenge:{},resendReadyAt:0};
    const context=vm.createContext({state,$:id=>nodes[id],Date,window:{setTimeout(){}},
      clearMessage(){},showChallenge(){},updateVerificationButtons(){},showMessage(node,text){node.textContent=text;},
      request:async()=>{throw Object.assign(new Error('Recoverable failure'),{status,retryAfter:60});}});
    vm.runInContext(step+resend,context);
    await handler();
    if(status===429){assert.equal(state.challengeToken,'old');}
    else{
      assert.equal(state.challengeToken,null);
      assert.equal(state.authChallenge,null);
      assert.equal(nodes['#mfa-code'].value,'');
      assert.equal(nodes['#login-form'].hidden,false);
      assert.equal(nodes['#mfa-form'].hidden,true);
    }
    assert.equal(nodes['#auth-message'].textContent,'Recoverable failure');
  }
});
