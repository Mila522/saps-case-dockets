import {escape as e,label,badge,date,field,link,empty,notice,facts,section,form,confirmAction} from './ui.mjs';
import {custodyActions,writable} from './contracts.mjs';

export async function evidenceDetail(ctx,id) {
  const item=await ctx.api.request(`/evidence/${id}`);
  const docket=await ctx.api.request(`/investigations/dockets/${item.docket_id}`);
  const [files,events,candidates]=await Promise.all([ctx.all(`/evidence/${id}/files`),
    ctx.has('evidence.view_custody')?ctx.all(`/evidence/${id}/custody-events`):Promise.resolve([]),ctx.all(`/evidence/${id}/custodians`)]);
  const names=new Map(candidates.map(person=>[person.id,person.username]));
  const officer=id=>id?(names.get(id)||id):'No custodian';
  const open=writable(docket)&&!['RELEASED','DISPOSED'].includes(item.status);
  const actions=open?custodyActions(item.status):[];
  const destinations=candidates.filter(person=>person.id!==item.current_custodian_officer_id);
  const custodyForm=actions.length?form('custody-form','Record custody event',field('event_type','Event type',{choices:actions})+
    field('to_custodian_officer_id','Destination custodian',{choices:[['','Select a same-station officer'],...destinations.map(person=>[person.id,`${person.rank} ${person.username}`])],required:false})+
    field('to_location','Destination location',{max:255})+field('notes','Event notes',{type:'textarea',wide:true,max:4000}),'Review custody event'):'';
  ctx.render(`<div class="page-heading"><div><p class="eyebrow">${e(docket.cas_number)} / Evidence</p><h1>${e(item.evidence_reference)}</h1><p>${badge(item.status)} <span class="muted">${item.is_digital?'Digital':'Physical'} evidence</span></p></div><div class="actions">${link('Back to docket',`/investigator/docket?id=${docket.id}`)}<button id="refresh" class="button button-secondary">Refresh evidence</button></div></div>
    <nav class="section-nav" aria-label="Evidence sections"><a href="#overview">Overview</a><a href="#files">File metadata</a><a href="#custody">Chain of custody</a></nav>
    <section class="panel custody-banner"><h2>Current custody</h2>${facts([['Custodian',officer(item.current_custodian_officer_id)],['Location',item.current_storage_location],['Status',label(item.status)]])}</section>
    ${!open?'<p class="notice success">This evidence is read-only. Recorded files and history remain available.</p>':''}
    ${section('overview',item.title,facts([['Evidence type',label(item.evidence_type)],['CAS reference',docket.cas_number],['Registered',date(item.registered_at)],['Collected',date(item.collected_at)],['Collected by',officer(item.collected_by_officer_id)],['Collection location',item.collection_location],['Description',item.description,true]]))}
    ${section('files',`File metadata (${files.length})`,`<p class="muted">Protected uploads are available. The server records filename, media type, positive byte size, SHA-256 and version. Recorded metadata cannot be edited or deleted.</p>`+(files.length?`<ol class="timeline">${files.map(file=>`<li><h3>Version ${file.file_version} · ${e(file.original_filename)}</h3>${facts([['Media type',file.media_type],['Size',`${file.file_size_bytes.toLocaleString()} bytes`],['Uploaded',date(file.uploaded_at)],['Uploaded by',file.uploaded_by_user_id===ctx.user.id?ctx.user.username:file.uploaded_by_user_id],['SHA-256',file.sha256_hash,true]])}<button class="button button-secondary download" data-file="${e(file.id)}">Download verified file</button></li>`).join('')}</ol>`:empty('No files recorded yet.')))}
    ${open?form('upload-form','Upload evidence file',field('file','Evidence file',{type:'file',wide:true,hint:'Select a non-empty file. Storage keys and credentials are never requested. The server enforces its configured size limit.'}),'Upload and register metadata'):''}
    ${section('custody',`Chain of custody (${events.length})`,!ctx.has('evidence.view_custody')?empty('Your account cannot view custody history.'):events.length?`<ol class="timeline">${events.map(event=>`<li><h3>${e(label(event.event_type))}</h3><p class="meta">${e(date(event.occurred_at))} · User ${e(event.performed_by_user_id===ctx.user.id?ctx.user.username:event.performed_by_user_id)}</p>${facts([['Previous custodian',officer(event.from_custodian_officer_id)],['New custodian',officer(event.to_custodian_officer_id)],['Previous location',event.from_location],['New location',event.to_location]])}<p>${e(event.event_notes||'No notes recorded')}</p></li>`).join('')}</ol>`:empty('No custody events recorded.'))}
    ${custodyForm}`,item.evidence_reference);
  document.querySelector('#refresh').onclick=()=>ctx.run(async()=>{await ctx.load();notice('Evidence refreshed.',true);});
  const eventType=document.querySelector('#event_type'),destination=document.querySelector('#to_custodian_officer_id');
  if(eventType) {
    const update=()=>{const transfer=eventType.value==='TRANSFERRED';destination.disabled=!transfer;destination.required=transfer;destination.closest('.field').hidden=!transfer;};
    eventType.onchange=update;
    document.querySelector('#custody-form').addEventListener('reset',()=>setTimeout(update,0));update();
  }
  ctx.bindForm('custody-form',async data=>{
    const copy=data.event_type==='TRANSFERRED'?`Transfer ${item.evidence_reference} from ${officer(item.current_custodian_officer_id)} to ${officer(data.to_custodian_officer_id)} at ${data.to_location}?`:`Record ${label(data.event_type)} for ${item.evidence_reference}?`;
    if(!await confirmAction('Confirm custody event',copy+' This event is permanent.')) return;
    await ctx.api.request(`/evidence/${id}/custody-events`,{method:'POST',body:{...data,expected_custody_event_id:item.custody_version}});
    await ctx.load();notice('Custody event recorded. Current custody and history refreshed.',true);
  });
  ctx.bindForm('upload-form',async (_,form)=>{
    const file=form.elements.file.files[0];
    if(!file || file.size<=0) throw Object.assign(new Error('Choose a file with a positive size.'),{fields:[{loc:['file'],msg:'Empty files cannot be registered.'}]});
    const body=new FormData();body.append('file',file);
    const result=await ctx.api.request(`/evidence/${id}/files`,{method:'POST',body});
    form.reset();
    await ctx.load();notice(`File uploaded. Metadata recorded as version ${result.file_version}.`,true);
  });
  document.querySelectorAll('.download').forEach(button=>button.onclick=()=>ctx.run(async()=>{
    const file=files.find(file=>file.id===button.dataset.file);
    const blob=await ctx.api.request(`/evidence/${id}/files/${file.id}/download`,{binary:true});
    const url=URL.createObjectURL(blob),anchor=document.createElement('a');
    anchor.href=url;anchor.download=file.original_filename;document.body.append(anchor);anchor.click();anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    await ctx.load();notice('Verified file downloaded. Access recorded in custody history.',true);
  }));
}
