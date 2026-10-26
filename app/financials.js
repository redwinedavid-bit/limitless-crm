const el=id=>document.getElementById(id);const val=id=>el(id).value.trim();
async function call(url,method='GET',body){const r=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data}
async function act(fn){try{el('feedback').textContent=JSON.stringify(await fn(),null,2);await refresh()}catch(e){el('feedback').textContent=e.message}}
async function refresh(){try{el('records').textContent=JSON.stringify({proposals:await call('/api/proposals'),invoices:await call('/api/invoices')},null,2)}catch(e){el('records').textContent='Login as owner/office first: '+e.message}}
el('refresh').onclick=refresh;el('review').onclick=()=>act(async()=>{const d=await call('/api/jobs/'+encodeURIComponent(val('job'))+'/financial-overview');el('summary').textContent=JSON.stringify(d,null,2);return d});
el('createProposal').onclick=()=>act(()=>call('/api/proposals','POST',{job_number:val('pjob'),number:val('pnum'),title:val('ptitle'),scope:val('pscope'),total:val('ptotal')}));
const today=()=>new Date().toISOString().slice(0,10);
function addLine(desc='',qty='1',price='',tax='0'){
 const row=document.createElement('div');row.className='invoice-line';row.style.cssText='display:flex;flex-wrap:wrap;gap:5px;padding:8px;border-bottom:1px solid #355760';
 const fields=[['description','Description',desc,'text'],['quantity','Qty',qty,'number'],['unit_price','Unit price',price,'number'],['tax_rate','Tax %',tax,'number']];
 for(const [key,place,value,type] of fields){const input=document.createElement('input');input.dataset.field=key;input.placeholder=place;input.type=type;input.value=value;input.step=type==='number'?'0.001':undefined;input.style.maxWidth=key==='description'?'275px':'95px';row.append(input)}
 const remove=document.createElement('button');remove.textContent='Remove';remove.type='button';remove.onclick=()=>row.remove();row.append(remove);el('line-items').append(row);
}
el('addLine').onclick=()=>addLine();
el('createInvoice').onclick=()=>act(()=>{
 const lines=[...document.querySelectorAll('.invoice-line')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-field]')].map(x=>[x.dataset.field,x.value])));
 const payload={job_number:val('ijob'),number:val('inum'),proposal_id:val('proposal')||null,issued_on:val('issuedOn')||null,due_on:val('dueOn')||null};
 if(lines.length)payload.lines=lines;
 if(val('iamount'))payload.amount=val('iamount');
 return call('/api/invoices','POST',payload)
});
el('approve').onclick=()=>act(()=>call('/api/proposals/'+encodeURIComponent(val('proposalId'))+'/approve','POST'));
el('issue').onclick=()=>act(()=>call('/api/invoices/'+encodeURIComponent(val('invoiceId'))+'/issue','POST'));
el('pay').onclick=()=>act(()=>call('/api/invoices/'+encodeURIComponent(val('paymentInvoice'))+'/payments','POST',{amount:val('payAmount'),reference:val('payRef'),paid_on:new Date().toISOString().slice(0,10)}));refresh();

el('lookupTransactions').onclick=async()=>{try{el('transactions').textContent=JSON.stringify(await call('/api/invoices/'+encodeURIComponent(val('adjustInvoice'))+'/transactions'),null,2)}catch(e){el('transactions').textContent=e.message}};
el('createCredit').onclick=()=>act(()=>call('/api/invoices/'+encodeURIComponent(val('adjustInvoice'))+'/credits','POST',{number:val('creditNumber'),amount:val('creditAmount'),reason:val('creditReason'),credited_on:today()}));
el('reverseBtn').onclick=()=>act(()=>call('/api/payments/'+encodeURIComponent(val('reversePayment'))+'/reverse','POST',{reference:val('reverseRef'),amount:val('reverseAmount'),reason:val('reverseReason'),reversed_on:today()}));
el('refundBtn').onclick=()=>act(()=>call('/api/invoices/'+encodeURIComponent(val('adjustInvoice'))+'/refunds','POST',{reference:val('refundRef'),amount:val('refundAmount'),reason:val('refundReason'),refunded_on:today()}));

fetch('/api/auth/me').then(r=>r.json()).then(u=>{const link=document.getElementById('admin-pricing-link');if(link)link.hidden=u.role!=='owner';}).catch(()=>{});
