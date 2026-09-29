export async function mountEmailStatus(root,request,complaintId=null){
  const heading=document.createElement('h3');heading.textContent='Case email updates';
  const message=document.createElement('p');message.setAttribute('role','status');
  root.replaceChildren(heading,message);
  try{
    const data=await request('/case-email/'+(complaintId?`complaints/${complaintId}/status`:'status'));
    message.textContent=data.email_verified
      ? 'Saved email verified. Queued updates are sent by the email worker; SMTP acceptance does not confirm inbox delivery.'
      : 'Email updates not sent: the saved email is not verified. This does not prevent recording or reviewing the complaint.';
    for(const row of data.deliveries){
      const line=document.createElement('p');
      line.textContent=`${row.event_type}: ${row.status} (${row.reason})`;
      root.append(line);
    }
  }catch{
    message.textContent='Email delivery status is currently unavailable.';
  }
}
