(()=>{
 'use strict';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const cash=x=>Number(x||0).toLocaleString('en-US',{style:'currency',currency:'USD'});
 const day=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 function summarize(rows,today){
  const groups={Draft:[],Outstanding:[],Overdue:[],Paid:[],Credits:[]};
  for(const row of rows){
   if(row.status==='Draft')groups.Draft.push(row);
   else if(Number(row.balance)>0){groups.Outstanding.push(row);if(row.due_on&&row.due_on<today)groups.Overdue.push(row);}
   else if(row.status==='Paid'&&Number(row.paid)>0)groups.Paid.push(row);
   else groups.Credits.push(row);
  }
  return groups;
 }
 const definitions={Draft:['Draft Invoices','Draft Total','amount'],Outstanding:['Outstanding Invoices','Balance Due','balance'],Overdue:['Overdue Invoices','Past-Due Balance','balance'],Paid:['Paid Invoices','Net Payments on Paid Invoices','paid'],Credits:['Credits & Settled','Customer Credit Balance','credit_balance']};
 async function get(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('Unable to load invoices. Refresh or sign in with financial access.');return r.json();}
 async function load(host){
  if(!host)return;
  const request={};host.invoiceRequest=request;const selected=host.invoiceStage||'Outstanding';
  host.innerHTML='<p role="status">Loading invoices…</p>';
  try{
   const [rows,jobs,customers]=await Promise.all([get('/api/invoices'),get('/api/jobs'),get('/api/customers')]);
   if(!host.isConnected||host.invoiceRequest!==request)return;
   const today=day(),groups=summarize(rows,today),keys=['Draft','Outstanding','Overdue','Paid'];if(groups.Credits.length)keys.push('Credits');
   const customerNames=new Map(customers.map(c=>[c.id,c.name]));const names=new Map(jobs.map(j=>[j.id,customerNames.get(j.customerId)||'']));
   const amount=key=>groups[key].reduce((sum,row)=>sum+Math.round(Number(row[definitions[key][2]]||0)*100),0)/100;
   host.innerHTML=`<section class="card" aria-label="Invoice Overview"><div class="cardhead"><h3>Invoices</h3><button class="outline small" data-invoice-refresh>Refresh</button></div><div class="kpis">${keys.map(key=>`<button class="kpi" data-invoice-stage="${key}" style="text-align:left;color:inherit;background:white"><small>${definitions[key][0]}</small><h2>${groups[key].length}</h2><strong>${cash(amount(key))}</strong><br><small>${definitions[key][1]}</small></button>`).join('')}</div><p class="subtle">All Invoices · Overdue is included in Outstanding. Draft totals include tax; outstanding amounts reflect payments and credits. Paid totals show net payments on fully paid invoices, not all cash received. Due dates use America/New_York (${esc(today)}).</p><div data-invoice-list aria-live="polite"></div><p><a href="/billing-desk">Open Billing Desk</a> · <a href="/call-pricing?mode=actual">Prepare an Invoice From a Call</a></p></section>`;
   function draw(key){
    host.invoiceStage=key;host.querySelectorAll('[data-invoice-stage]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.invoiceStage===key)));
    const list=[...groups[key]].sort((a,b)=>String(a.due_on||'9999').localeCompare(String(b.due_on||'9999'))||a.number.localeCompare(b.number));
    host.querySelector('[data-invoice-list]').innerHTML=`<h4>${definitions[key][0]}</h4>`+(list.length?list.map(i=>`<article style="border-top:1px solid #e4e9eb;padding:14px 0"><strong><a href="/billing-desk?invoice=${encodeURIComponent(i.id)}">${esc(i.number)}</a> · ${esc(names.get(i.job_number)||i.job_number)}</strong><p><a href="/?page=jobs&amp;job=${encodeURIComponent(i.job_number)}">${esc(i.job_number)}</a> · ${esc(i.status)}${i.due_on?' · Due '+esc(i.due_on):''}</p><p>Total ${cash(i.amount)} · Balance Due ${cash(i.balance)}${Number(i.credit_balance)>0?' · Customer Credit '+cash(i.credit_balance):''}</p><a href="/billing-desk?invoice=${encodeURIComponent(i.id)}">Open Invoice</a> · <a href="/api/invoices/${encodeURIComponent(i.id)}/pdf" target="_blank" rel="noopener">View PDF</a></article>`).join(''):'<p>No invoices in this group.</p>');
   }
   host.querySelectorAll('[data-invoice-stage]').forEach(b=>b.onclick=()=>draw(b.dataset.invoiceStage));host.querySelector('[data-invoice-refresh]').onclick=()=>load(host);draw(keys.includes(selected)?selected:'Outstanding');
  }catch(e){if(host.isConnected&&host.invoiceRequest===request){host.innerHTML='<p role="alert"></p><button class="outline small">Retry Invoices</button>';host.querySelector('p').textContent=e.message;host.querySelector('button').onclick=()=>load(host);}}
 }
 window.LMSInvoiceDashboard={load,summarize};
})();
