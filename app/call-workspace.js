(async()=>{
 const by=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const wo=decodeURIComponent(location.pathname.split('/').pop()),base='/api/calls/'+encodeURIComponent(wo);
 let team=[],assignments=[],state,clock,offset=0,mileRevision=0,noteDraft,partRequest=crypto.randomUUID(),noteRequest=crypto.randomUUID(),poRequest=crypto.randomUUID();
 const msg=t=>by('message').textContent=t;
 const tabs=[...document.querySelectorAll('[data-panel]')];
 function showPanel(key){
  if(!tabs.some(t=>t.dataset.panel===key))key='details';
  for(const tab of tabs){const active=tab.dataset.panel===key;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;by('panel-'+tab.dataset.panel).hidden=!active;}
 }
 tabs.forEach((tab,index)=>{
  tab.onclick=()=>{showPanel(tab.dataset.panel);history.replaceState(null,'','#'+tab.dataset.panel);};
  tab.onkeydown=event=>{let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();tabs[next].focus();tabs[next].click();};
 });
 showPanel(location.hash.slice(1));
 window.addEventListener('hashchange',()=>showPanel(location.hash.slice(1)));
 async function api(url,method='GET',body){const r=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const x=await r.json();if(!r.ok)throw Error(typeof x.detail==='string'?x.detail:'Check the entered fields');return x;}
 async function act(button,fn){button.disabled=true;try{await fn();}catch(e){msg(e.message);}finally{button.disabled=false;if(button.id==='clock-button')drawClock();}}
 function localInput(value){const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
 function drawClock(){
  if(window.LMSFieldOffline?.localClock()){const local=window.LMSFieldOffline.localClock();by('clock-button').textContent=local.work_order_id===wo?'Clock out (device record)':'Offline clock on another call';by('clock-button').disabled=local.work_order_id!==wo;return;}
  if(state.role!=='technician'){by('clock-section').hidden=true;return;}
  by('clock-button').disabled=!!clock && clock.work_order_id!==wo;
  by('clock-button').textContent=clock?(clock.work_order_id===wo?'Clock out':'Clock running on another call'):'Clock in';
  by('clock-context').innerHTML=clock?`Started ${esc(new Date(clock.started_at).toLocaleString())}${clock.work_order_id!==wo?` · <a href="/calls/${encodeURIComponent(clock.work_order_id)}">Open running call</a>`:''}`:'';
  tick();
 }
 function tick(){const local=window.LMSFieldOffline?.localClock();if(local){const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(local.started_at))/1000));by('elapsed').textContent=[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(n=>String(n).padStart(2,'0')).join(':')+' · device record';drawClock();return;}if(!clock){by('elapsed').textContent='Clocked out';return;}const secs=Math.max(0,Math.floor((Date.now()+offset-Date.parse(clock.started_at))/1000));by('elapsed').textContent=[Math.floor(secs/3600),Math.floor(secs/60)%60,secs%60].map(n=>String(n).padStart(2,'0')).join(':');}
 async function syncClock(){try{const s=await api('/api/time/clock');clock=s.active;offset=Date.parse(s.server_now)-Date.now();drawClock();}catch(e){by('clock-button').disabled=true;msg('Clock status unavailable. Any running clock continues; reconnect and refresh.');}}
 function previewMiles(){const start=by('odo-start'),end=by('odo-end');const invalid=end.value!==''&&start.value!==''&&Number(end.value)<Number(start.value);end.setCustomValidity(invalid?'Finish must be at least start.':'');by('miles').textContent=start.value===''||end.value===''||invalid?'—':(Number(end.value)-Number(start.value)).toFixed(2);}
 async function notes(){const list=await api(base+'/notes');by('note-list').innerHTML=list.map(n=>`<div class="note"><span class="badge ${n.audience==='internal'?'internal':''}">${n.audience==='internal'?'Internal only':'Customer-facing'}</span> <strong>${esc(n.kind.replaceAll('_',' '))}</strong><br>${esc(n.text)}<br><small>${esc(n.author)} · ${esc(new Date(n.created_at).toLocaleString())}</small></div>`).join('')||'<p>No classified notes yet.</p>';}
 async function parts(){const list=await api(base+'/parts-used');by('used-list').innerHTML=list.map(p=>`<div class="usage">${p.active?'':'<span class="badge internal">Removed from usage</span> '}<strong>${esc(p.quantity)} ${esc(p.unit)} — ${esc(p.description)}</strong><br><small>${esc(p.author)}</small>${p.internal_note?`<p class="help">Internal: ${esc(p.internal_note)}</p>`:''}${p.active&&(state.role==='owner'||p.author_id===state.user_id)?`<button class="secondary" data-void="${esc(p.id)}">Correct / remove line</button>`:''}</div>`).join('')||'<p>No parts reported yet.</p>';
  by('used-list').querySelectorAll('[data-void]').forEach(button=>button.onclick=()=>act(button,async()=>{const p=list.find(p=>p.id===button.dataset.void);if(!confirm('Mark this line as removed? It will remain in the audit history.'))return;await api(base+'/parts-used/'+p.id,'PUT',{part_id:p.part_id,description:p.description,unit:p.unit,quantity:p.quantity,internal_note:p.internal_note,expected_revision:p.revision,active:false});await parts();msg('Line removed from active usage. Add a corrected line if needed.');await readiness();}));}
 async function loadAssignments(){
  assignments=(await api('/api/dispatch')).filter(a=>a.work_order_id===wo&&a.status!=='Cancelled');
  by('assignment-list').innerHTML=assignments.map(a=>`<p><strong>${esc(a.technician)}</strong><br>${esc(new Date(a.start).toLocaleString())} → ${esc(new Date(a.end).toLocaleString())}</p>`).join('')||'<p>No technician assigned yet.</p>';
  const current=by('manual-person').value;
  by('manual-person').innerHTML=team.filter(t=>assignments.some(a=>a.technician_id===t.id)).map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  if([...by('manual-person').options].some(o=>o.value===current))by('manual-person').value=current;
 }
 async function timeHistory(){const rows=await api('/api/work-orders/'+wo+'/time');const total=rows.reduce((n,t)=>n+t.hours,0),approved=rows.filter(t=>t.approved).reduce((n,t)=>n+t.hours,0);by('time-total').textContent=rows.length+' entries · '+total.toFixed(2)+' total hours · '+approved.toFixed(2)+' approved · '+(total-approved).toFixed(2)+' awaiting approval';by('time-list').innerHTML=rows.map(t=>`<p><strong>${t.hours.toFixed(2)} hours</strong> · ${esc(t.technician)}<br>${esc(new Date(t.started_at).toLocaleString())} → ${esc(new Date(t.ended_at).toLocaleString())}<br>${t.note?esc(t.note)+'<br>':''}${t.approved?'Approved':'Awaiting approval'}${t.needs_review?' · Long entry: review required':''}</p>`).join('')||'No completed time yet.';
  const targets=rows.map(t=>({key:'entry:'+t.id,label:new Date(t.started_at).toLocaleString()+' · '+t.hours+' hours',start:t.started_at,end:t.ended_at}));
  if(clock?.work_order_id===wo)targets.unshift({key:'clock:'+clock.id,label:'Current running clock',start:clock.started_at,end:new Date(Date.now()+offset).toISOString()});
  by('correction-target').innerHTML=targets.map(t=>`<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');by('correction-target').onchange=()=>{const t=targets.find(t=>t.key===by('correction-target').value);if(t){by('correction-start').value=localInput(t.start);by('correction-end').value=localInput(t.end);}};by('correction-target').onchange();
  if(['owner','technician'].includes(state.role)){const requests=await api('/api/time/corrections');by('correction-list').innerHTML=requests.filter(r=>r.work_order_id===wo).map(r=>`<p>${esc(r.status)}: ${esc(r.reason)}${r.review_note?' · '+esc(r.review_note):''}</p>`).join('');}
 }
 async function readiness(){if(state.role!=='owner')return;const s=await api(base+'/workspace');state.readiness=s.readiness;by('ready-state').innerHTML=`<p><strong>${esc(s.readiness.stage)}</strong></p>`+s.readiness.blockers.map(b=>`<p class="alert">• ${esc(b)}</p>`).join('')+(s.readiness.invoice_number?`<p>Invoice: ${esc(s.readiness.invoice_number)} · ${esc(s.readiness.invoice_status)}</p>`:'');by('confirm-parts').disabled=!s.readiness.pricing_revision;}
 try{
  state=await api(base+'/workspace');const me=await api('/api/auth/me');state.user_id=me.id;
  if(['owner','office'].includes(state.role)){
   team=await api('/api/technicians');by('assignment-section').hidden=false;by('manual-person-label').hidden=false;
   by('assign-person').innerHTML='<option value="">Choose team member</option>'+team.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
   await loadAssignments();
   by('assignment-form').onsubmit=e=>{e.preventDefault();act(by('assign-save'),async()=>{await api('/api/work-orders/'+wo+'/assignments','POST',{technician_id:by('assign-person').value,scheduled_start:new Date(by('assign-start').value).toISOString(),scheduled_end:new Date(by('assign-end').value).toISOString()});await loadAssignments();msg('Technician assigned to this call.');});};
  }
  clock=state.active_clock;offset=Date.parse(state.server_now)-Date.now();
  by('call-po-context').textContent='Job '+state.call.job_number+' — '+state.call.summary+'. This PO will belong to this job only.';
  by('call-po-purchases').href='/purchasing?job='+encodeURIComponent(state.call.job_number);
  const canBuy=['DR','AR','DB'].includes(me.po_prefix);by('call-po-form').hidden=!canBuy;
  by('call-po-permission').textContent=canBuy?'Your '+me.po_prefix+' PO number is assigned when you create the order.':'An admin must assign your PO designator in Employee Costs before you can create a PO.';
  by('call-po-form').onsubmit=e=>{e.preventDefault();act(by('call-po-submit'),async()=>{
   const po=await api(base+'/purchase-orders','POST',{request_id:poRequest,vendor:by('call-po-vendor').value,description:by('call-po-description').value,amount:by('call-po-amount').value});
   poRequest=crypto.randomUUID();by('call-po-form').reset();by('call-po-result').textContent='Issued '+po.number+' to '+po.vendor+' for job '+po.job_number+'.';msg('Purchase Order '+po.number+' created.');
  });};
  by('title').textContent=state.call.job_number+' · '+state.call.summary;by('customer').textContent=state.customer.name+(state.location?.name?' · '+state.location.name:'');by('address').textContent=state.location?.address||'No address recorded';by('status').value=state.call.status;by('field-form').href='/field?work_order='+encodeURIComponent(wo);
  const phone=(state.location?.phone||state.customer.phone).replace(/[^+\d]/g,'');by('contact-actions').innerHTML=(phone?`<a class="action" href="tel:${esc(phone)}">Call customer</a>`:'')+(state.location?.address?`<a class="action" href="https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(state.location.address)}" target="_blank" rel="noopener noreferrer">Directions</a>`:'');
  by('equipment').innerHTML=state.equipment.map(a=>`<p><strong>${esc(a.asset_tag)}</strong> · ${esc(a.manufacturer)} ${esc(a.model)}<br>Serial: ${esc(a.serial)}</p>`).join('')||'No equipment linked.';
  by('legacy-notes').innerHTML=state.notes.map(n=>`<div class="note"><small>Earlier service note · ${esc(n.author)}</small><br>${esc(n.note)}</div>`).join('');
  const mile=state.mileage[0];mileRevision=mile?.revision||0;by('odo-start').value=mile?.start_odometer??'';by('odo-end').value=mile?.end_odometer??'';const readonly=!!mile?.invoiced;by('odo-start').disabled=readonly;by('odo-end').disabled=readonly;by('save-odometer').disabled=readonly;by('odo-state').textContent=mile?.invoiced?'Mileage is linked to an invoice.':'Shared call mileage: any team member with access can save the start and finish.';previewMiles();drawClock();
  if(['owner','technician'].includes(state.role)){const catalog=await api('/api/field/parts-catalog');by('used-part').innerHTML='<option value="">Unlisted part</option>'+catalog.map(p=>`<option value="${esc(p.id)}">${esc(p.sku)} — ${esc(p.description)}</option>`).join('');}else{by('used-part-form').hidden=true;by('note-form').hidden=true;}
  noteDraft=new LMSDraft('/api/drafts/'+wo+'/field-note',()=>({text:by('note-text').value,kind:by('note-kind').value,audience:by('note-audience').value}),body=>{by('note-text').value=body.text||'';by('note-kind').value=body.kind||'service_description';by('note-audience').value=body.audience||'internal';},text=>by('note-draft-state').textContent=text);await noteDraft.init();
  await Promise.all([notes(),parts(),timeHistory()]);
  if(state.role==='owner'){
   by('customer-history-link').href='/profitability?customer='+encodeURIComponent(state.customer.id);by('back').href='/calls';by('back').textContent='← Calls';by('admin-links').hidden=false;by('admin-readiness').hidden=false;await readiness();
   by('call-time-review').href='/time-review?call='+encodeURIComponent(wo);by('admin-pricing').hidden=false;by('tab-review').textContent='Review & Bill';
   by('quote-call').href='/call-pricing?call='+encodeURIComponent(wo)+'&mode=estimate';
   by('invoice-call').href='/call-pricing?call='+encodeURIComponent(wo)+'&mode=actual';
  }
 }catch(e){msg(e.message);return;}
 async function loadSystems(){const assets=await api(base+'/service-systems');by('service-system').innerHTML='<option value="">Choose a system</option>'+assets.filter(a=>!a.linked).map(a=>`<option value="${esc(a.id)}">${esc(a.location)} · ${esc(a.asset_tag)} · ${esc(a.model)}</option>`).join('');by('equipment').innerHTML=assets.filter(a=>a.linked).map(a=>`<p><strong>${esc(a.asset_tag)}</strong> · ${esc(a.manufacturer)} ${esc(a.model)}</p>`).join('')||'No equipment linked.';}
 by('link-service-system').onclick=e=>act(e.target,async()=>{if(!by('service-system').value)throw Error('Choose the system serviced.');await api(base+'/service-systems/'+by('service-system').value,'POST');await loadSystems();msg('System linked to this visit.');});loadSystems().catch(e=>msg(e.message));
 by('odo-start').oninput=previewMiles;by('odo-end').oninput=previewMiles;
 by('save-odometer').onclick=e=>act(e.target,async()=>{previewMiles();if(!by('odo-start').value||!by('odo-start').reportValidity()||!by('odo-end').reportValidity())throw Error('Enter a valid starting odometer.');const r=await api('/api/work-orders/'+wo+'/mileage','PUT',{start_odometer:by('odo-start').value,end_odometer:by('odo-end').value||null,expected_revision:mileRevision});mileRevision=r.revision;by('odo-state').textContent=r.complete?'Saved: '+r.miles+' miles.':'Starting reading saved.';msg('Odometer saved.');await readiness();});
 by('clock-button').onclick=e=>act(e.target,async()=>{if(clock&&clock.work_order_id!==wo)return;await api('/api/time/clock/'+(clock?'stop':'start'),'POST',clock?{clock_id:clock.id}:{work_order_id:wo});await syncClock();await timeHistory();msg(clock?'Clock started.':'Clocked out. Time saved.');});
 by('refresh-clock').onclick=syncClock;
 by('used-part').onchange=()=>by('custom-part').hidden=!!by('used-part').value;
 by('used-part-form').onsubmit=e=>{e.preventDefault();act(e.submitter,async()=>{await api(base+'/parts-used','POST',{client_request_id:partRequest,part_id:by('used-part').value||null,description:by('used-description').value,unit:by('used-unit').value,quantity:by('used-quantity').value,internal_note:by('used-note').value});partRequest=crypto.randomUUID();by('used-note').value='';await parts();await readiness();msg('Part added to call.');});};
 by('note-kind').onchange=()=>{by('note-audience').value=by('note-kind').value==='service_description'?'customer':'internal';noteDraft.changed();};by('note-form').addEventListener('input',()=>noteDraft.changed());
 by('note-form').onsubmit=e=>{e.preventDefault();act(e.submitter,async()=>{const body={text:by('note-text').value,kind:by('note-kind').value,audience:by('note-audience').value,client_request_id:noteRequest};await noteDraft.flush();await api(base+'/notes','POST',body);noteRequest=crypto.randomUUID();if(by('note-text').value===body.text)by('note-text').value='';await noteDraft.checkpoint(noteDraft.getBody());await notes();msg(body.audience==='internal'?'Internal note saved.':'Customer-facing note saved.');});};
 by('preview-customer').onclick=e=>act(e.target,async()=>{const s=await api(base+'/customer-summary');by('customer-preview').hidden=false;by('customer-preview').innerHTML='<h3>Customer-facing content</h3>'+s.notes.map(n=>`<p class="note">${esc(n.note)}</p>`).join('');});
 by('save-status').onclick=e=>act(e.target,async()=>{await api('/api/work-orders/'+wo+'/status','PATCH',{status:by('status').value});msg('Status saved.');await readiness();});
 by('manual-time').onsubmit=e=>{e.preventDefault();act(e.submitter,async()=>{await api('/api/work-orders/'+wo+'/time','POST',{technician_id:['owner','office'].includes(state.role)?by('manual-person').value:null,started_at:new Date(by('manual-start').value).toISOString(),ended_at:new Date(by('manual-end').value).toISOString(),note:by('manual-note').value,mileage:0});by('manual-time').reset();await timeHistory();await readiness();msg('Time entry added. Add another entry for the next day or visit.');});};
 by('correction-form').onsubmit=e=>{e.preventDefault();act(e.submitter,async()=>{const [kind,id]=by('correction-target').value.split(':');await api('/api/time/corrections','POST',{entry_id:kind==='entry'?id:null,clock_id:kind==='clock'?id:null,started_at:new Date(by('correction-start').value).toISOString(),ended_at:new Date(by('correction-end').value).toISOString(),reason:by('correction-reason').value});await timeHistory();msg('Correction sent for admin review. Your time has not been changed.');});};
 by('confirm-parts').onclick=e=>act(e.target,async()=>{await api('/api/admin/calls/'+wo+'/parts-review','PUT',{expected_pricing_revision:state.readiness.pricing_revision});await readiness();msg('Parts review confirmed.');});
 setInterval(tick,1000);setInterval(()=>{if(state.role==='technician'&&!document.hidden)syncClock();},30000);window.addEventListener('online',()=>{if(state.role==='technician')syncClock();});window.addEventListener('offline',()=>{msg('Offline. Field changes and clock-out can be retained on this device. Synchronize before finishing the call.');});
})();


