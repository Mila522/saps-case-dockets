import {field,form,notice,confirmAction,empty} from './ui.mjs';
import {writable} from './contracts.mjs';

export async function invitations(ctx) {
  if(!ctx.has('feedback.provide')) throw Object.assign(new Error('Permission denied.'),{status:403});
  const rows=(await ctx.all('/investigations/dockets')).filter(writable);
  let requestId=crypto.randomUUID();
  ctx.render(`<h1>Invite complainant</h1><p>Select an assigned docket to invite its complainant to the station.</p>${rows.length?form('invitation-form','Station invitation',
    field('docket_id','Assigned docket',{choices:rows.map(row=>[row.id,`${row.cas_number} — ${row.complaint_reference}`])})+
    field('purpose','Appointment purpose',{choices:[['INTERVIEW','Interview'],['MEETING','Meeting']]})+
    field('starts_at','Appointment date and time',{type:'datetime-local',hint:'Your local time. The email will show South African time.'}),
    'Queue invitation email',true):empty('No open assigned dockets are available for invitations.')}`,'Invite complainant');
  ctx.bindForm('invitation-form',async data=>{
    if(!await confirmAction('Send station invitation?','A meeting/interview invitation with the station address and appointment time will be queued to the saved verified complainant email.')) return;
    const result=await ctx.api.request(`/investigations/dockets/${data.docket_id}/invitations`,{method:'POST',body:{
      request_id:requestId,purpose:data.purpose,starts_at:new Date(data.starts_at).toISOString()}});
    requestId=crypto.randomUUID();
    notice(result.delivery_status==='PENDING'?'Invitation recorded and email queued.':`Invitation recorded. Email ${result.delivery_status}: ${result.delivery_reason}.`,result.delivery_status==='PENDING');
  });
}
