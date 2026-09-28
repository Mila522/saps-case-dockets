import {downloadPrivate} from '/portal/submission-extras.mjs';
const mounts=new WeakMap();
const label=value=>value.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
function text(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}
function render(parent,value){
  if(value==null){text(parent,'p','Not yet created.');return;}
  if(Array.isArray(value)){
    if(!value.length)text(parent,'p','No records yet.');
    for(const row of value){const article=document.createElement('article');article.className='dossier-record';parent.append(article);render(article,row);}return;
  }
  const dl=document.createElement('dl');parent.append(dl);
  for(const [key,item] of Object.entries(value)){
    if(key==='id'||key.endsWith('_id'))continue;
    text(dl,'dt',label(key));const dd=text(dl,'dd','');
    if(item&&typeof item==='object')render(dd,item);
    else dd.textContent=item==null?'Not recorded':key.endsWith('_at')?new Date(item).toLocaleString('en-ZA'):typeof item==='boolean'?(item?'Yes':'No'):String(item);
  }
}
function downloadButton(parent,title,path,filename,request){
  const button=text(parent,'button',title);button.type='button';button.className='button button-secondary';
  button.onclick=async()=>{button.disabled=true;try{await downloadPrivate(request,path,filename);}catch(error){text(parent,'p',error.message).setAttribute('role','alert');}finally{button.disabled=false;}};
}
function activity(parent,rows){
  text(parent,'p','Recorded access and changes, oldest first. Each entry identifies who acted and when. Earlier actions can only appear if the system recorded them.');
  const list=document.createElement('ol');list.className='docket-activity';parent.append(list);
  for(const row of rows){const item=document.createElement('li');list.append(item);
    text(item,'strong',label(row.action.replaceAll('.',' ')));
    text(item,'p',row.actor);text(item,'small',new Date(row.occurred_at).toLocaleString('en-ZA'));
  }
  if(!rows.length)text(parent,'p','No activity recorded.');
}
async function mount(parent,path,request){
  const generation={};mounts.set(parent,generation);
  parent.classList.add('full-dossier');parent.textContent='Loading complete case record...';
  const data=await request(path);if(mounts.get(parent)!==generation)return data;parent.textContent='';
  text(parent,'h2',data.docket?.cas_number || data.complaint.reference_number);
  text(parent,'p','Complete case record - complainant details, original submissions, decisions and subsequent activity.');
  for(const [name,value] of Object.entries(data)){
    const section=document.createElement('details');section.open=['complaint','complainant','statements','witnesses','complainant_uploads','activity_trail'].includes(name);
    text(section,'summary',name==='activity_trail'?'Who accessed or changed this case':label(name));parent.append(section);
    if(name==='activity_trail')activity(section,value);else render(section,value);
    if(name==='complainant_uploads')for(const file of value)downloadButton(section,`Download ${file.original_filename}`,`/complaints/${data.complaint.id}/uploads/${file.id}/download`,file.original_filename,request);
    if(name==='evidence')for(const item of value)for(const file of item.files)downloadButton(section,`Download evidence: ${file.original_filename}`,`/dockets/${data.docket.id}/files/${file.id}/download`,file.original_filename,request);
  }
  return data;
}
export const mountFullDocket=(parent,id,request)=>mount(parent,`/dockets/${id}/full`,request);
export const mountComplaintDossier=(parent,id,request)=>mount(parent,`/complaints/${id}/dossier`,request);
