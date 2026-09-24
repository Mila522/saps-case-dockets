import {escape as e,badge,date,field,link,table,empty,notice} from './ui.mjs';

export async function queue(ctx) {
  const rows=await ctx.all('/investigations/dockets');
  const stations=[...new Set(rows.map(row=>row.station_name))];
  ctx.render(`<div class="page-heading"><div><p class="eyebrow">Investigation / Work queue</p><h1>Assigned dockets</h1><p class="intro">Review your cases. Record the next step. Keep the evidence traceable.</p><p class="muted">Station: ${e(stations.join(', ')||'Available when a docket is assigned')}</p></div><button id="refresh" class="button button-secondary">Refresh queue</button></div>
    <section class="panel" aria-labelledby="queue-heading"><div class="panel-heading"><h2 id="queue-heading">Your assignments</h2><span class="count">${rows.length} assigned</span></div>
    <div class="filters">${field('search','Search CAS or complaint reference',{required:false,type:'search'})}${field('status-filter','Docket status',{required:false,choices:[['','All statuses'],...[...new Set(rows.map(row=>row.status))].sort()]})}<button id="clear" class="button button-secondary">Clear filters</button></div><p id="result-count" class="muted" role="status"></p><div id="dockets"></div></section>`,'Assigned dockets');
  const search=document.querySelector('#search'), status=document.querySelector('#status-filter');
  function draw() {
    const term=search.value.trim().toLowerCase();
    const filtered=rows.filter(row=>(!status.value||row.status===status.value) && `${row.cas_number} ${row.complaint_reference}`.toLowerCase().includes(term));
    document.querySelector('#result-count').textContent=`Showing ${filtered.length} of ${rows.length} assignments`;
    document.querySelector('#dockets').innerHTML=filtered.length?table(['Docket / complaint','Crime category','Status','Assignment / station','Last updated','Action'],filtered.map(row=>[
      `<strong>${e(row.cas_number)}</strong><small>${e(row.complaint_reference)}</small>`,e(row.crime_category),badge(row.status),`${e(date(row.assigned_at))}<small>${e(row.station_name)}</small>`,e(date(row.updated_at)),link('Open docket',`/investigator/docket?id=${row.id}`)
    ])):empty(rows.length?'No dockets match these filters.':'No dockets assigned yet. Your commander’s assignments will appear here.');
  }
  search.oninput=draw;status.onchange=draw;
  document.querySelector('#clear').onclick=()=>{search.value='';status.value='';draw();search.focus();};
  document.querySelector('#refresh').onclick=()=>ctx.run(async()=>{await ctx.load();notice('Assigned dockets refreshed.',true);});
  draw();
}
