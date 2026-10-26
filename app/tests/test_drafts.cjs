/* Run with node tests/test_drafts.cjs. No browser or network required. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function setup(fetch){const context={window:{},fetch,setTimeout,clearTimeout};vm.runInNewContext(fs.readFileSync('drafts.js','utf8'),context);return context.window.LMSDraft;}
const response=(body,status=200)=>({ok:status<400,status,json:async()=>body});
(async()=>{
 let body={text:'first'},stored={},revision=0,release,requests=[];
 const Draft=setup(async(url,options)=>{requests.push(options.method);if(options.method==='GET')return response({revision,body:stored});const data=JSON.parse(options.body);assert.equal(data.expected_revision,revision);if(!release)await new Promise(resolve=>release=resolve);stored=data.body;return response({revision:++revision,body:stored});});
 const d=new Draft('/api/drafts/call/field-note',()=>body,b=>body=b,()=>{});await d.init();body={text:'editing'};const saving=d.flush();await Promise.resolve();body={text:'latest edit during save'};release();await saving;assert.equal(stored.text,'latest edit during save');assert.equal(revision,2);assert.deepEqual(requests,['GET','PUT','PUT']);
 const checkpointBody=JSON.parse(JSON.stringify(body));await d.checkpoint(checkpointBody);assert.deepEqual(stored,{});assert.equal(body.text,'latest edit during save');
 let ready;body={text:''};const Early=setup(async()=>{await new Promise(resolve=>ready=resolve);return response({revision:1,body:{text:'old draft'}});});const early=new Early('/api/drafts/call/note',()=>body,b=>body=b,()=>{});const init=early.init();body={text:'typed while loading'};early.dirty=true;ready();await init;clearTimeout(early.timer);assert.equal(body.text,'typed while loading');
 let puts=0;const Conflict=setup(async(url,opts)=>opts.method==='GET'?response({revision:1,body:{}}):(puts++,response({detail:'Newer draft exists'},409)));const conflict=new Conflict('/api/drafts/call/note',()=>({text:'mine'}),()=>{},()=>{});await conflict.init();conflict.last='';await assert.rejects(()=>conflict.flush(),/Newer draft/);assert.equal(conflict.ready,false);await assert.rejects(()=>conflict.flush(),/not ready/);assert.equal(puts,1);
 console.log('Draft checks passed: edits during save, safe checkpoint, edits during load, revision conflict.');
})().catch(e=>{console.error(e);process.exitCode=1;});
