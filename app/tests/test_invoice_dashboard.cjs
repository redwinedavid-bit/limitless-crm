const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('invoice-dashboard.js','utf8');
function setup(fetch){const c={window:{},fetch,Intl,Date};vm.createContext(c);vm.runInContext(source,c);return c.window.LMSInvoiceDashboard;}
test('invoice groups use balances, keep overdue within outstanding and separate credits from paid',()=>{
 const {summarize}=setup();const row=(id,status,balance,due_on,paid=0)=>({id,status,balance,due_on,paid});
 const g=summarize([row('draft','Draft',100,'2020-01-01'),row('late','Partially Paid',50,'2026-10-25',50),row('today','Issued',100,'2026-10-26'),row('nodue','Issued',30,null),row('paid','Paid',0,'2020-01-01',100),row('credit','Credited',0,'2020-01-01'),row('overpaid','Credit Balance',0,null,120)],'2026-10-26');
 assert.deepEqual(Array.from(g.Draft,x=>x.id),['draft']);assert.equal(g.Outstanding.length,3);assert.deepEqual(Array.from(g.Overdue,x=>x.id),['late']);assert.deepEqual(Array.from(g.Paid,x=>x.id),['paid']);assert.equal(g.Credits.length,2);
});
test('dashboard shows invoice links, escaped customer names and refreshable empty states',async()=>{
 const refresh={},list={innerHTML:''},buttons=['Draft','Outstanding','Overdue','Paid'].map(stage=>({dataset:{invoiceStage:stage},setAttribute(){}}));
 const host={isConnected:true,innerHTML:'',querySelector:s=>s==='[data-invoice-refresh]'?refresh:list,querySelectorAll:()=>buttons};
 const fixtures={'/api/invoices':[{id:'i1',number:'INV-1',job_number:'LMS1',status:'Issued',amount:100,balance:40,due_on:'2020-01-01'}],'/api/jobs':[{id:'LMS1',customerId:'c1'}],'/api/customers':[{id:'c1',name:'<Customer>'}]};
 const api=setup(async url=>({ok:true,json:async()=>fixtures[url]}));await api.load(host);
 assert.match(host.innerHTML,/\$40.00/);assert.match(list.innerHTML,/billing-desk\?invoice=i1/);assert.match(list.innerHTML,/&lt;Customer&gt;/);
 buttons[0].onclick();assert.match(list.innerHTML,/No invoices/);await refresh.onclick();assert.equal(host.invoiceStage,'Draft');assert.match(list.innerHTML,/No invoices/);
});
