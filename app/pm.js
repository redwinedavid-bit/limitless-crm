const $=id=>document.getElementById(id);const status=t=>$('status').textContent=t;
async function req(path,options={}){const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...options});let v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:JSON.stringify(v.detail));return v}
const form=(id,handler)=>$(id).addEventListener('submit',async e=>{e.preventDefault();try{await handler(new FormData(e.currentTarget));status('Saved successfully')}catch(err){status('Error: '+err.message)}});
$('refresh').onclick=async()=>{try{$('plans').textContent=JSON.stringify(await req('/api/pm/plans'),null,2)}catch(e){status(e.message)}};
$('rates').onclick=async()=>{try{$('rates-out').textContent=JSON.stringify(await req('/api/labor-rates'),null,2)}catch(e){status(e.message)}};
form('plan',async f=>{await req('/api/pm/plans',{method:'POST',body:JSON.stringify({equipment_id:f.get('equipment_id'),interval_days:Number(f.get('interval_days')),next_due:f.get('next_due'),checklist:f.get('checklist').split('\n').map(x=>x.trim()).filter(Boolean)})});$('refresh').click()});
form('generate',async f=>{const r=await req('/api/pm/plans/'+encodeURIComponent(f.get('plan_id'))+'/work-order',{method:'POST',body:JSON.stringify({job_number:f.get('job_number')})});status('Created work order '+r.work_order_id)});
form('rate',async f=>{await req('/api/labor-rates',{method:'PUT',body:JSON.stringify({technician_id:f.get('technician_id'),hourly_cost:f.get('hourly_cost'),overtime_multiplier:f.get('overtime_multiplier'),mileage_rate:f.get('mileage_rate')||'0'})})});
form('approve',async f=>{await req('/api/time/'+encodeURIComponent(f.get('entry_id'))+'/approve',{method:'POST',body:JSON.stringify({overtime_hours:f.get('overtime_hours')||'0'})})});
form('cost',async f=>{$('cost-out').textContent=JSON.stringify(await req('/api/jobs/'+encodeURIComponent(f.get('job_number'))+'/field-costs'),null,2)});
