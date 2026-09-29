// Shared complaint intake: optional witnesses, private evidence and required vehicle documents.
export function mountSubmissionExtras(form, request) {
  const group=document.createElement('fieldset');
  group.innerHTML=`<legend>Evidence and optional witnesses</legend>
    <div data-vehicle hidden><label>Vehicle number plate<input data-plate maxlength="30" placeholder="e.g. ND 123 456"></label>
    <label>Vehicle registration document (required for vehicle theft)<input data-registration type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.odt"></label></div>
    <label>Evidence files (optional)<input data-evidence type="file" multiple accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.txt,.odt,.csv"></label>
    <p>Attach photographs, video, documents or voice recordings. Up to 20 files per complaint, including the vehicle document.</p>
    <details><summary>Witness details (optional)</summary><div data-witnesses></div><button type="button" data-add-witness>Add a witness</button></details>
    <p data-upload-status role="status"></p>`;
  form.querySelector('[type=submit]').before(group);
  const category=form.querySelector('[name=crime_category]');
  const plate=group.querySelector('[data-plate]'), registration=group.querySelector('[data-registration]');
  const sync=()=>{const vehicle=category.value==='Vehicle theft';group.querySelector('[data-vehicle]').hidden=!vehicle;plate.required=vehicle;registration.required=vehicle;};
  category.addEventListener('change',sync);sync();
  group.querySelector('[data-add-witness]').onclick=()=>{
    const row=document.createElement('fieldset'); row.dataset.witness='';
    row.innerHTML=`<legend>Witness</legend><label>First name<input data-key="first_name" required maxlength="100"></label><label>Last name<input data-key="last_name" required maxlength="100"></label><label>Phone (optional)<input data-key="phone_number" type="tel" maxlength="30"></label><label>Email (optional)<input data-key="email" type="email" maxlength="254"></label><label>Address (optional)<input data-key="address" maxlength="4000"></label><label>Statement (optional)<textarea data-key="statement_text" maxlength="20000"></textarea></label><button type="button">Remove witness</button>`;
    row.querySelector('button').onclick=()=>row.remove();group.querySelector('[data-witnesses]').append(row);
  };
  const cache=new WeakMap();
  return async()=>{
    const vehicle=category.value==='Vehicle theft';
    if(vehicle&&(!plate.value.trim()||!registration.files.length))throw new Error('Vehicle theft requires a number plate and registration document.');
    const files=[...group.querySelector('[data-evidence]').files].map(file=>[file,'EVIDENCE']);
    if(vehicle)files.push([registration.files[0],'VEHICLE_REGISTRATION']);
    if(files.length>20)throw new Error('Attach at most 20 files.');
    const witnesses=[...group.querySelectorAll('[data-witness]')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-key]')].map(n=>[n.dataset.key,n.value.trim()]).filter(([,v])=>v)));
    const upload_ids=[];
    for(const [file,purpose] of files){
      group.querySelector('[data-upload-status]').textContent=`Uploading ${file.name}...`;
      if(!cache.has(file)){const body=new FormData();body.append('file',file);body.append('purpose',purpose);cache.set(file,await request('/complaints/uploads',{method:'POST',body}));}
      upload_ids.push(cache.get(file).id);
    }
    group.querySelector('[data-upload-status]').textContent='Files ready. Registering complaint...';
    return {witnesses,upload_ids,...(vehicle?{vehicle_number_plate:plate.value.trim()}:{})};
  };
}

export async function downloadPrivate(request,path,filename){
  const blob=await request(path,{binary:true});const url=URL.createObjectURL(blob);
  const link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export async function mountUploads(parent,complaintId,request){
  const files=await request(`/complaints/${complaintId}/uploads`);
  const title=document.createElement('h3');title.textContent='Complainant evidence and vehicle documents';parent.append(title);
  if(!files.length){const p=document.createElement('p');p.textContent='No files submitted.';parent.append(p);}
  for(const file of files){const button=document.createElement('button');button.type='button';button.textContent=`Download ${file.original_filename}${file.purpose==='VEHICLE_REGISTRATION'?' (vehicle registration)':''}`;parent.append(button);
    button.onclick=async()=>{button.disabled=true;try{await downloadPrivate(request,`/complaints/${complaintId}/uploads/${file.id}/download`,file.original_filename);}catch(error){const p=document.createElement('p');p.setAttribute('role','alert');p.textContent=error.message;parent.append(p);}finally{button.disabled=false;}};
  }
}
