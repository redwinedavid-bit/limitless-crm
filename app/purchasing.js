(()=>{'use strict';
const el=id=>document.getElementById(id), money=n=>Number(n||0).toLocaleString('en-US',{style:'currency',currency:'USD'});
const escape=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const current=new Date().toISOString().slice(0,10);document.querySelectorAll('input[type=date]').forEach(x=>x.value=current);
let activePO='',currentJob='',user=null;
async function getBuyerNumber(){let n=await api('my-number');el('po-preview').value=n.next_preview||'Designator not assigned';return n;}
async function api(path,method='GET',body){let response=await fetch('/api/purchasing/'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});let value=await response.json().catch(()=>({}));if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail||value));return value;}
function report(message,fail=false){let x=el('feedback');x.textContent=message;x.classList.toggle('error',fail);}
function metric(label,value,note=''){return `<div class="p-kpi"><small>${escape(label)}</small><strong>${escape(value)}</strong><small>${escape(note)}</small></div>`;}
function payload(form){return Object.fromEntries(new FormData(form).entries());}
async function jobLoad(){if(!currentJob)return;const j=await api('jobs/'+encodeURIComponent(currentJob));el('results').hidden=false;
el('metrics').innerHTML=metric('Historical Direct Cost',money(j.historical_direct_cost),'Preserved sheet rollups')+metric('Approved Field Labor & Mileage',money(j.approved_field_cost))+metric('New PO Actual',money(j.new_po_actual),'Net receipts and vendor bills')+metric('Open PO Commitments',money(j.new_po_commitment))+metric('Projected Direct Cost',money(j.projected_cost))+metric('Projected Gross Profit',money(j.projected_profit),'Against recorded contract');
let pos=j.purchase_orders;if(user.role==='technician'){el('metrics').innerHTML='';}el('po-list').innerHTML=`<table><thead><tr><th>PO Number</th><th>Vendor</th><th>Status</th><th>Amount</th><th>Actual</th><th>Committed</th><th>View</th></tr></thead><tbody>${pos.map(p=>`<tr><td>${escape(p.number)}</td><td>${escape(p.vendor)}</td><td><span class="p-pill">${escape(p.status)}</span></td><td>${money(p.amount)}</td><td>${p.historical_cost_already_in_job_totals?'In historical totals':money(p.actual_cost)}</td><td>${money(p.open_commitment)}</td><td><button type="button" data-po="${escape(p.number)}">Details</button></td></tr>`).join('')}</tbody></table>`;
el('po-list').querySelectorAll('[data-po]').forEach(b=>b.onclick=()=>openPO(b.dataset.po));}
async function openPO(number){activePO=number;let p=await api('pos/'+encodeURIComponent(number));el('po-detail').classList.add('active');el('detail-title').textContent=`${p.number} · ${p.vendor} · ${p.job_number}`;
el('detail-summary').innerHTML=metric('PO Value',money(p.amount))+metric('Status',p.status)+metric('Gross Received',money(p.gross_received))+metric('Returned',money(p.returned))+metric('Vendor Invoiced',money(p.vendor_invoiced))+metric('Vendor Credits',money(p.vendor_credits))+metric('Actual Job Cost',money(p.actual_cost))+metric('Remaining Commitment',money(p.open_commitment));
let buttons=[];
if(p.source==='historical')buttons.push('<span class="p-warning">Historical PO: read-only. Already included in imported job totals.</span>');
else{
  if(['Issued','Partially Received','Received'].includes(p.status)&&(['owner','office'].includes(user.role)||p.buyer_designator===user.po_prefix))buttons.push(['Close Remaining Balance','close']);
 if(p.status==='Issued'&&p.gross_received===0&&(['owner','office'].includes(user.role)||p.buyer_designator===user.po_prefix))buttons.push(['Cancel PO','cancel']);
}
el('actions').replaceChildren();buttons.forEach(b=>{if(Array.isArray(b)){let bt=document.createElement('button');bt.textContent=b[0];bt.dataset.action=b[1];bt.onclick=async()=>{let data; if(['close','cancel'].includes(b[1])){const reason=prompt('Reason for '+b[1]+' (required):');if(!reason)return;data={reason};}await perform('pos/'+encodeURIComponent(number)+'/'+b[1],data);};el('actions').append(bt);}else{let span=document.createElement('span');span.textContent='Historical PO is read-only; costs are already included in historical rollups.';el('actions').append(span);}});
el('attachments').innerHTML=(p.attachments||[]).map(a=>`<div class="p-event"><a href="${escape(a.download_url)}" target="_blank" rel="noopener">${escape(a.filename)}</a> · ${escape(a.kind)} · ${Math.ceil(a.size_bytes/1024)} KB</div>`).join('')||'<p class="p-note">No receipts attached.</p>';
el('attachment-form').querySelector('button').disabled=!(['owner','office'].includes(user.role)||p.buyer_designator===user.po_prefix);
const events=[['Receipts',p.receipts],['Vendor invoices',p.vendor_invoices],['Returns',p.returns],['Vendor credits',p.vendor_credits_history]];
el('history').innerHTML=events.map(([title,items])=>`<h3>${escape(title)} (${items.length})</h3>${items.map(x=>`<div class="p-event">${escape(x.date)} · ${escape(x.reference)} · <strong>${money(x.amount)}</strong>${x.matched_receipt_value!==undefined?' · Matched '+money(x.matched_receipt_value):''}${x.reason?' · '+escape(x.reason):''}${x.variance_reason?' · '+escape(x.variance_reason):''}</div>`).join('')||'<p class="p-note">No records yet.</p>'}`).join('');
const readonly=p.source==='historical', status=p.status;
el('receipt-form').querySelector('button').disabled=readonly||!['Issued','Partially Received','Received'].includes(status)||!(['owner','office'].includes(user.role)||p.buyer_designator===user.po_prefix);
el('bill-form').querySelector('button').disabled=readonly||!['Partially Received','Received','Closed'].includes(status)||!['owner','office','bookkeeper'].includes(user.role);
el('return-form').querySelector('button').disabled=readonly||!['Partially Received','Received','Closed'].includes(status)||!(['owner','office'].includes(user.role)||p.buyer_designator===user.po_prefix);
el('credit-form').querySelector('button').disabled=readonly||p.vendor_invoiced<=p.vendor_credits||!['owner','office','bookkeeper'].includes(user.role);
el('po-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
async function perform(path,body){try{await api(path,'POST',body);report('Saved successfully. Job costs updated.');await jobLoad();if(activePO)await openPO(activePO);}catch(e){report(e.message,true);}}
el('search').onsubmit=async ev=>{ev.preventDefault();currentJob=el('job').value.trim().toUpperCase();el('new-job').value=currentJob;try{await jobLoad();report('Loaded job '+currentJob+'.');}catch(e){report(e.message,true);}};
el('create').onsubmit=async ev=>{ev.preventDefault();let data=payload(ev.target);try{let p=await api('pos','POST',data);currentJob=p.job_number;el('job').value=currentJob;el('new-job').value=currentJob;await jobLoad();await openPO(p.number);await getBuyerNumber();report('Issued '+p.number+' directly to '+p.job_number+'. No manager approval needed.');}catch(e){report(e.message,true);}};
for(const [id,path] of [['receipt-form','receipts'],['bill-form','vendor-invoices'],['return-form','returns'],['credit-form','credits']]){
 el(id).onsubmit=async ev=>{ev.preventDefault();if(!activePO)return report('Open a PO first',true);let data=payload(ev.target);try{await api('pos/'+encodeURIComponent(activePO)+'/'+path,'POST',data);ev.target.reset();ev.target.querySelector('input[type=date]').value=current;await jobLoad();await openPO(activePO);report('Transaction recorded; job costs updated.');}catch(e){report(e.message,true);}};
}
el('attachment-form').onsubmit=async ev=>{ev.preventDefault();if(!activePO)return report('Open a PO first',true);
 const form=new FormData(ev.target);try{const response=await fetch('/api/purchasing/pos/'+encodeURIComponent(activePO)+'/attachments',{method:'POST',credentials:'same-origin',body:form});const result=await response.json().catch(()=>({}));if(!response.ok)throw Error(result.detail||'Upload failed');ev.target.reset();await openPO(activePO);report('Receipt/photo saved to '+activePO+'.');}catch(e){report(e.message,true);}};
fetch('/api/auth/me',{credentials:'same-origin'}).then(r=>{if(!r.ok)throw Error('Sign in to the CRM on the Dashboard first');return r.json()}).then(async u=>{user=u;
if(!['owner','office','estimator','bookkeeper'].includes(u.role)&&!u.po_prefix)throw Error('Purchasing access denied for this account');
if(!u.po_prefix)el('create').querySelector('button').disabled=true;
await getBuyerNumber();
report('Signed in as '+u.name+' ('+u.role+'). PO designator: '+(u.po_prefix||'Not assigned')+'. Enter a job number to review purchases.');let q=new URLSearchParams(location.search).get('job');if(q){el('job').value=q;el('search').requestSubmit();}}).catch(e=>{user={role:'none'};report(e.message,true);});
})();
