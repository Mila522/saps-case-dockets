import {escape as e,label,badge,date,field,check,link,table,empty,notice,facts,section,form,confirmAction} from './ui.mjs';
import {NOTE_TYPES,EVIDENCE_TYPES,writable} from './contracts.mjs';
import {mountMaterials} from '/portal/case-materials.mjs';

export async function docketDetail(ctx,id) {
  const docket=await ctx.api.request(`/investigations/dockets/${id}`);
  const [notes,history,items]=await Promise.all([
    ctx.all(`/dockets/${id}/notes`),ctx.all(`/investigations/dockets/${id}/status-history`),
    ctx.has('evidence.manage')?ctx.all(`/dockets/${id}/evidence`):Promise.resolve([])
  ]);
  const open=writable(docket);
  const noteForm=open&&ctx.has('case.add_note')?form('note-form','Add investigation note',
    field('note_type','Note type',{choices:NOTE_TYPES})+field('content','Note',{type:'textarea',max:20000,wide:true,hint:'0 / 20,000 characters. Notes cannot be edited or deleted; append a correction if needed.'})+check('is_sensitive','Mark this note as sensitive'),'Save note'):'';
  const statuses=docket.allowed_next_statuses||[];
  const statusForm=statuses.length?form('status-form','Change docket status',field('status','Next status',{choices:statuses})+
    field('reason','Reason / closure reason',{type:'textarea',wide:true,max:4000,hint:'Required for every change. When closing, this is the permanent closure reason.'}),'Review status change'):'<p class="muted">No status changes are available for this docket and your permissions.</p>';
  const evidenceForm=open&&ctx.has('evidence.manage')?form('evidence-form','Register evidence',
    field('title','Title',{max:255})+field('evidence_type','Evidence type',{choices:EVIDENCE_TYPES})+check('is_digital','Digital evidence')+
    field('description','Description',{type:'textarea',max:20000,wide:true})+
    field('collected_at','Collection date and time (optional)',{type:'datetime-local',required:false,hint:'Enter your device’s local time.'})+
    field('collection_location','Collection location (optional)',{required:false,max:4000})+field('storage_location','Current storage location',{max:255}),'Register evidence'):'';
  ctx.render(`<div class="page-heading"><div><p class="eyebrow">${e(docket.station_name)} / Docket</p><h1>${e(docket.cas_number)}</h1><p>${badge(docket.status)} <span class="muted">Complaint ${e(docket.complaint_reference)}</span></p></div><div class="actions">${link('Assigned dockets','/investigator/')}<button id="refresh" class="button button-secondary">Refresh docket</button></div></div>
    <nav class="section-nav" aria-label="Docket sections"><a href="#overview">Overview</a><a href="#notes">Investigation notes</a><a href="#evidence">Evidence</a><a href="#status-history">Status history</a></nav>
    ${!open?'<p class="notice success">This docket is read-only. Its recorded history remains available.</p>':''}
    ${section('overview','Docket overview',facts([
      ['Crime category',docket.crime_category],['Station',docket.station_name],['Opened',date(docket.opened_at)],
      ['Assigned',date(docket.assigned_at)],['Investigating officer',docket.investigating_officer_id===ctx.user.officer_id?ctx.user.username:docket.investigating_officer_id],['Last update',date(docket.updated_at)],
      ['Incident date',date(docket.incident_occurred_at)],['Incident location',docket.incident_location],['Complaint reference',docket.complaint_reference],
      ['Incident description',docket.incident_description,true],...(docket.closed_at?[['Closed',date(docket.closed_at)],['Closure reason',docket.closure_reason,true]]:[])
    ]))}
    ${section('notes',`Investigation notes (${notes.length})`,notes.length?`<ol class="timeline">${notes.map(note=>`<li><h3>${e(label(note.note_type))} ${note.is_sensitive?'<span class="count">Sensitive</span>':''}</h3><p class="meta">${e(date(note.created_at))} · Officer ${e(note.author_officer_id===ctx.user.officer_id?ctx.user.username:note.author_officer_id)}</p><p>${e(note.content)}</p></li>`).join('')}</ol>`:empty('No investigation notes recorded yet.'))}
    ${noteForm}
    ${section('materials','Complainant statement and witnesses','<div id="case-materials"></div>')}
    ${section('evidence',`Evidence register (${items.length})`,ctx.has('evidence.manage')?`<div class="filters">${field('evidence-type-filter','Evidence type',{required:false,choices:[['','All types'],...EVIDENCE_TYPES]})}${field('evidence-status-filter','Evidence status',{required:false,choices:[['','All statuses'],...[...new Set(items.map(item=>item.status))]]})}${field('digital-filter','Format',{required:false,choices:[['','All formats'],['true','Digital'],['false','Physical']]})}</div><div id="evidence-list"></div>`:empty('Your account cannot access the evidence register.'))}
    ${evidenceForm}
    ${section('status-history','Status history',history.length?`<ol class="timeline">${history.map(row=>`<li><h3>${e(row.from_status?label(row.from_status)+' → ':'')}${e(label(row.to_status))}</h3><p class="meta">${e(date(row.changed_at))} · User ${e(row.changed_by_user_id===ctx.user.id?ctx.user.username:row.changed_by_user_id)}</p><p>${e(row.change_reason||'No reason recorded')}</p></li>`).join('')}</ol>`:empty('No status changes recorded.'))}
    ${statusForm}`,docket.cas_number);
  document.querySelector('#refresh').onclick=()=>ctx.run(async()=>{await ctx.load();notice('Docket refreshed.',true);});
  if(ctx.has('evidence.manage')) {
    const type=document.querySelector('#evidence-type-filter'), status=document.querySelector('#evidence-status-filter'),digital=document.querySelector('#digital-filter');
    const draw=()=>{
      const filtered=items.filter(item=>(!type.value||type.value===item.evidence_type)&&(!status.value||status.value===item.status)&&(!digital.value||digital.value===String(item.is_digital)));
      document.querySelector('#evidence-list').innerHTML=filtered.length?table(['Evidence','Type / format','Status','Collected','Current custody','Action'],filtered.map(item=>[
        `<strong>${e(item.evidence_reference)}</strong><small>${e(item.title)}</small>`,`${e(label(item.evidence_type))}<small>${item.is_digital?'Digital':'Physical'}</small>`,badge(item.status),e(date(item.collected_at)),`${e(item.current_custodian_officer_id===ctx.user.officer_id?ctx.user.username:item.current_custodian_officer_id||'No current custodian')}<small>${e(item.current_storage_location||'Not recorded')}</small>`,link('View evidence',`/investigator/evidence?id=${item.id}`)
      ])):empty(items.length?'No evidence matches these filters.':'No evidence registered yet.');
    };
    type.onchange=draw;status.onchange=draw;digital.onchange=draw;draw();
  }
  const content=document.querySelector('#note-form textarea');
  if(content) {
    const count=()=>{document.querySelector('#content-hint').textContent=`${content.value.length.toLocaleString()} / 20,000 characters. Notes are append-only.`;};
    content.oninput=count;document.querySelector('#note-form').addEventListener('reset',()=>setTimeout(count,0));
  }
  ctx.bindForm('note-form',async data=>{
    await ctx.api.request(`/dockets/${id}/notes`,{method:'POST',body:data});
    await ctx.load();notice('Investigation note saved.',true);
  });
  ctx.bindForm('status-form',async data=>{
    if(!await confirmAction('Change docket status?',`${docket.cas_number}: ${label(docket.status)} → ${label(data.status)}. ${data.status==='CLOSED'?'Closing makes this docket read-only. ':''}This change and its reason become part of the permanent history.`)) return;
    await ctx.api.request(`/dockets/${id}/status`,{method:'POST',body:{...data,expected_status:docket.status}});
    await ctx.load();notice('Docket status updated and recorded in history.',true);
  });
  ctx.bindForm('evidence-form',async data=>{
    if(data.collected_at && new Date(data.collected_at)>new Date()) throw Object.assign(new Error('Collection date cannot be in the future.'),{fields:[{loc:['collected_at'],msg:'Choose a date in the past.'}]});
    const item=await ctx.api.request(`/dockets/${id}/evidence`,{method:'POST',body:data});
    await ctx.load();notice(`Evidence registered: ${item.evidence_reference}`,true);
  });
  // Bind case forms before loading the independently rendered dossier panel.
  // Otherwise a fast submit can perform native navigation while handlers wait.
  const materialsPanel = document.querySelector('#case-materials');
  void mountMaterials(materialsPanel,docket.complaint_id,(path,options)=>ctx.api.request(path,options))
    .catch(()=>{materialsPanel.textContent='Unable to load statements. Refresh the docket to retry.';});

}
