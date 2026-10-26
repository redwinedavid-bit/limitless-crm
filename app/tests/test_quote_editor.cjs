const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('quote editor always maps the standard layout and preserves optional quote details',()=>{
 const source=fs.readFileSync('estimates-workspace.js','utf8'),start=source.indexOf('function presentationFromEditor()'),end=source.indexOf("$('payment-50').onclick=",start),fields=new Map();
 const context={$:id=>{if(!fields.has(id))fields.set(id,{value:''});return fields.get(id);},Date};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 context.loadPresentation({proposal_date:'2026-10-24',valid_until:'2026-11-24',equipment_schedule:[{component:'Heat pump',description:'Confirmed model | accessory'}],deposit_percent:'50'});
 let data=context.presentationFromEditor();assert.equal(data.template,'limitless');assert.equal(data.proposal_date,'2026-10-24');assert.equal(data.equipment_schedule[0].description,'Confirmed model | accessory');assert.equal(data.deposit_amount,null);
 fields.get('deposit-amount').value='100';assert.throws(()=>context.presentationFromEditor(),/not both/);
 context.loadPresentation({proposal_date:'2026-10-24'});data=context.presentationFromEditor();assert.equal(data.equipment_schedule.length,0);assert.equal(data.deposit_percent,null);assert.equal(data.deposit_amount,null);
 fields.get('equipment-schedule').value='Missing separator';assert.throws(()=>context.presentationFromEditor(),/Component/);
 const html=fs.readFileSync('estimates-workspace.html','utf8');assert.doesNotMatch(html,/id="quote-template"|id="template-command"|Classic CRM/);assert.match(html,/Every quote PDF automatically/);
});
