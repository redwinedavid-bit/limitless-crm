const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('job activity isolates entries, shows pending approval and uses approved server costs',async()=>{
 const button={},host={isConnected:true,innerHTML:'',querySelector:()=>button};
 const orders=[{id:'a',job_number:'LMS1',summary:'<motor>',status:'Open'},{id:'b',job_number:'LMS2',summary:'Other',status:'Open'}];
 const fixtures={'/api/work-orders':orders,'/api/time/entries':[{work_order_id:'a',technician:'Danny',hours:2,approved:false,started_at:'2026-10-26T12:00:00Z'},{work_order_id:'b',technician:'Other',hours:9,approved:false}],'/api/time/mileage':[{work_order_id:'a',technician:'Danny',start_odometer:100,end_odometer:120,miles:20,complete:true,approved:false}],'/api/jobs/LMS1/financial-overview':{historical_and_approved_actual_cost:50,open_new_po_commitment:75}};
 const context={window:{},fetch:async path=>({ok:true,json:async()=>fixtures[path]}),console};vm.createContext(context);vm.runInContext(fs.readFileSync('job-activity.js','utf8'),context);
 await context.window.LMSJobActivity.load(host,'LMS1','owner');
 assert.match(host.innerHTML,/2 entries awaiting approval/);assert.match(host.innerHTML,/2\.00 hours/);assert.match(host.innerHTML,/20\.00 miles/);assert.match(host.innerHTML,/\$50\.00/);assert.match(host.innerHTML,/&lt;motor&gt;/);assert.doesNotMatch(host.innerHTML,/Other|9\.00 hours/);assert.match(host.innerHTML,/time-review\?job=LMS1&call=a/);assert.match(host.innerHTML,/mode=actual/);
 fixtures['/api/time/entries'][0].approved=true;fixtures['/api/time/mileage'][0].approved=true;
 await button.onclick();assert.match(host.innerHTML,/0 entries awaiting approval/);
 await context.window.LMSJobActivity.load(host,'LMS1','office');assert.doesNotMatch(host.innerHTML,/employee-costs|Prepare invoice/);
});
