const el=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let rates=[];
async function api(url,method='GET',body){const r=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the entered values');return data;}
function feedback(text,error=false){el('feedback').textContent=text;el('feedback').className=error?'error':'';}
async function load(){
  try {
    const [time,mileage,costRates,orders,invoices]=await Promise.all([api('/api/time/entries'),api('/api/time/mileage'),api('/api/labor-rates'),api('/api/work-orders'),api('/api/invoices')]);
    rates=costRates;
    await loadCorrections();
    el('invoice-call').innerHTML=orders.map(w=>`<option value="${esc(w.id)}">${esc(w.job_number)} · ${esc(w.summary)}</option>`).join('');
    el('mileage-invoice-id').innerHTML=invoices.filter(i=>i.status==='Draft').map(i=>`<option value="${esc(i.id)}">${esc(i.number)} · ${esc(i.job_number)}</option>`).join('');
    const orderMap=new Map(orders.map(w=>[w.id,`${w.job_number} · ${w.summary}`]));
    const technicians=new Map([...time,...mileage].map(r=>[r.technician_id,r.technician]));
    for(const r of rates)technicians.set(r.technician_id,r.name);
    const selected=el('technician').value;
    el('technician').innerHTML=[...technicians].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
    if(technicians.has(selected))el('technician').value=selected;
    showRate();
    el('time').innerHTML=time.length?time.map(t=>`<article><strong>${esc(orderMap.get(t.work_order_id)||t.work_order_id)}</strong><p>${esc(t.technician)} · ${t.hours.toFixed(2)} hours<br>${esc(new Date(t.started_at).toLocaleString())} → ${esc(new Date(t.ended_at).toLocaleString())}${t.mileage?`<br>Legacy mileage: ${t.mileage} miles`:''}</p>${t.needs_review?'<p class="pending">Over 24 hours: review before approval.</p>':''}${t.approved?'<p>Approved</p>':`<label>Overtime hours <input id="ot-${esc(t.id)}" type="number" min="0" max="${t.hours}" step="0.01" value="0"></label><button data-time="${esc(t.id)}">Approve time</button>`}</article>`).join(''):'<p>No completed call time.</p>';
    el('mileage').innerHTML=mileage.length?mileage.map((r,i)=>`<article><strong>${esc(orderMap.get(r.work_order_id)||r.work_order_id)}</strong><p>${esc(r.technician)} · ${r.start_odometer} → ${r.end_odometer??'Awaiting finish'} · ${r.complete?r.miles+' miles':'Incomplete'} · Revision ${r.revision}</p>${r.approved?`<p>Approved${r.invoiced?' · Linked to invoice':''}</p>`:r.complete?`<button data-mileage="${i}">Approve mileage</button>`:'<p>Ending odometer required before approval.</p>'}</article>`).join(''):'<p>No call mileage recorded.</p>';
    document.querySelectorAll('[data-time]').forEach(b=>b.onclick=()=>action(b,()=>{const input=el('ot-'+b.dataset.time);if(!input.reportValidity())throw Error('Check overtime hours');return api('/api/time/'+b.dataset.time+'/approve','POST',{overtime_hours:Number(input.value)});}));
    document.querySelectorAll('[data-mileage]').forEach(b=>b.onclick=()=>action(b,()=>{const r=mileage[Number(b.dataset.mileage)];return api('/api/work-orders/'+r.work_order_id+'/mileage/'+r.technician_id+'/approve','POST',{expected_revision:r.revision});}));
  }catch(e){feedback('Sign in with office financial access. '+e.message,true);}
}
function showRate(){const r=rates.find(r=>r.technician_id===el('technician').value);el('hourly').value=r?.hourly_cost??'';el('mileage-rate').value=r?.mileage_rate??'';el('overtime-multiplier').value=r?.overtime_multiplier??1.5;}
async function action(button,fn){button.disabled=true;try{await fn();feedback('Saved.');await load();}catch(e){feedback(e.message,true);}finally{button.disabled=false;}}
el('technician').onchange=showRate;
el('reload').onclick=load;
el('rates').onsubmit=e=>{e.preventDefault();action(e.target.querySelector('button'),()=>api('/api/labor-rates','PUT',{technician_id:el('technician').value,hourly_cost:Number(el('hourly').value),mileage_rate:Number(el('mileage-rate').value),overtime_multiplier:Number(el('overtime-multiplier').value)}));};
el('mileage-invoice').onsubmit=e=>{e.preventDefault();action(e.target.querySelector('button'),()=>api('/api/invoices/'+el('mileage-invoice-id').value+'/call-mileage','POST',{work_order_id:el('invoice-call').value,unit_price:el('mileage-selling-rate').value,tax_rate:el('mileage-tax').value}));};
el('remove-mileage').onclick=e=>action(e.target,()=>api('/api/invoices/'+el('mileage-invoice-id').value+'/call-mileage/'+el('invoice-call').value,'DELETE'));
load();

async function loadCorrections(){
 const me=await api('/api/auth/me');if(me.role!=='owner')return;
 el('corrections-section').hidden=false;const rows=await api('/api/time/corrections');
 el('corrections').innerHTML=rows.filter(r=>r.status==='Pending').map(r=>`<article><strong>${esc(r.technician)}</strong><p>${esc(r.reason)}</p><p>Requested: ${esc(new Date(r.started_at).toLocaleString())} → ${esc(new Date(r.ended_at).toLocaleString())}</p><p>Original: ${esc(r.original.started_at)} → ${esc(r.original.ended_at||'Running clock')}</p><label>Admin review note<input id="review-note-${esc(r.id)}" minlength="3" maxlength="2000"></label><button data-correction="${esc(r.id)}" data-decision="approve">Approve correction</button> <button data-correction="${esc(r.id)}" data-decision="reject">Reject</button></article>`).join('')||'<p>No pending time corrections.</p>';
 el('corrections').querySelectorAll('[data-correction]').forEach(b=>b.onclick=()=>action(b,()=>{const note=el('review-note-'+b.dataset.correction).value;if(note.trim().length<3)throw Error('Enter an admin review note.');return api('/api/time/corrections/'+b.dataset.correction+'/review','POST',{decision:b.dataset.decision,note});}));
}
