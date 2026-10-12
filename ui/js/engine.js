/* =====================================================================
   STUDIO ENGINE — the free helper that runs on the user's own computer
   (127.0.0.1:8765). This page sends it jobs and shows live progress.
   Paired by a token the engine puts in the address (#engine=…) when it
   opens this page. If it is not running, the script-download flow stays.
   ===================================================================== */
const ENG={url:(location.protocol==='http:'&&location.port)?location.origin:'http://127.0.0.1:8765',  // the app's page is served by its own engine
 token:'',on:false,hw:null,jobs:[],es:null,waiters:{},checking:false};
const SND_DEFAULTS={strength:60,lufs:-16,hp:true,presence:true,deess:true,comp:true,roomtone:true};
function engToken(){
  const m=location.hash.match(/engine=([\w-]+)/);
  if(m){try{localStorage.setItem('engineToken',m[1])}catch(e){}history.replaceState(null,'',location.pathname+location.search+location.hash.replace(/[#&]?engine=[\w-]+/,''));if(location.hash==='#')history.replaceState(null,'',location.pathname+location.search)}
  try{return m?m[1]:(localStorage.getItem('engineToken')||'')}catch(e){return m?m[1]:''}
}
function engFileUrl(p){return `${ENG.url}/api/file?t=${encodeURIComponent(ENG.token)}&path=${encodeURIComponent(p)}`}
async function engFetch(path,opts={}){
  const r=await fetch(ENG.url+path,{...opts,headers:{'Content-Type':'application/json','X-Engine-Token':ENG.token,...(opts.headers||{})},body:opts.body&&typeof opts.body!=='string'?JSON.stringify(opts.body):opts.body});
  const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||d.detail||('Engine error '+r.status));return d;
}
const baseName=p=>String(p||'').split(/[\\/]/).pop();
/* choose a file: the app's own window (Studio Pro desktop) or the engine's file window */
async function engChoose(kind){
  if(window.studioApp&&window.studioApp.pick){const path=await window.studioApp.pick(kind);return {path:path||''}}
  return engFetch('/api/pick',{method:'POST',body:{kind}});
}
function engSay(t){stuSay('#engStatus',t)}

async function engCheck(){
  if(ENG.checking)return;ENG.checking=true;
  try{
    const ctl=new AbortController();const tm=setTimeout(()=>ctl.abort(),2500);
    const r=await fetch(ENG.url+'/api/hello',{headers:{'X-Engine-Token':ENG.token},signal:ctl.signal});clearTimeout(tm);
    const d=await r.json();
    if(!d.paired){engOff('The Studio Engine did not accept this window. Close Studio Pro and open it again.');return}
    if(!ENG.on){ENG.on=true;await engConnected(d)}
  }catch(e){engOff('Starting the Studio Engine…')}
  finally{ENG.checking=false}
}
function engOff(msg){
  ENG.on=false;if(ENG.es){ENG.es.close();ENG.es=null}engSay(msg);engShow();
}
async function engConnected(hello){
  engSay(`Engine connected ✓ (version ${hello.version}) — checking this computer…`);engShow();
  ENG.es=new EventSource(`${ENG.url}/api/events?t=${encodeURIComponent(ENG.token)}`);
  ENG.es.onmessage=e=>{try{ENG.jobs=JSON.parse(e.data)}catch(err){return}engJobsRender();engResolve()};
  ENG.es.onerror=()=>{if(ENG.es&&ENG.es.readyState===2){engOff('The Studio Engine stopped — Studio Pro is restarting it…')}};
  try{ENG.hw=await engFetch('/api/hardware');engHwRender();engSay(`Engine connected ✓ — ${ENG.hw.best_encoder_name} for video, voice isolation ${ENG.hw.deep_filter?'ready':'not installed'}.`)}catch(e){engSay('Engine connected ✓')}
  if(STU.engPrep)engLoadPrepared(STU.engPrep,true).catch(()=>{});
  if(STU.anaFile)fetch(engFileUrl(STU.anaFile)).then(r=>r.ok?r.json():null).then(d=>{if(d){anaSet(d);stuStats()}}).catch(()=>{});
  // after a page reload: show the last finished render, and follow one that is still running
  try{const jobs=await engFetch('/api/jobs');const r=jobs.find(j=>j.type==='render'&&STU.engVideo&&j.params&&j.params.video===STU.engVideo);
    if(r&&r.state==='done')engShowQC(r.result);else if(r&&['queued','running'].includes(r.state)){ENG.jobs=jobs;engWaitRender(r.id)}}catch(e){}
}
function engShow(){
  const on=ENG.on;if($('#picOn')){$('#picOn').hidden=!on;$('#picOff').hidden=on}$('#engOff').hidden=on;$('#stu_clean').closest('label').hidden=on;$('#engOn').hidden=!on;$('#engFiles').hidden=!on;$('#stuManual1').hidden=on;
  $('#stuStep2').hidden=on;$('#engSoundOn').hidden=!on;$('#engSoundOff').hidden=on;$('#engRenderBox').hidden=!on;
  $('#engAudioRow').hidden=!STU.settings.separate;
  $('#engVideoPath').textContent=STU.engVideo?`${baseName(STU.engVideo)} — ${STU.engVideo}`:'No video chosen';
  $('#engAudioPath').textContent=STU.engAudio?`${baseName(STU.engAudio)} — ${STU.engAudio}`:'No audio chosen';
}
function engHwRender(){const h=ENG.hw;if(!h)return;$('#engHw').innerHTML=h.summary.map(esc).join('<br>')}

/* ---------- jobs ---------- */
const JOB_NAMES={report:'Check files',prepare:'Prepare (preview + sound)',sync:'Find sync',clean:'Clean the sound',preview:'Sound preview',render:'Make the final files'};
function engJobsRender(){
  const box=$('#engJobs');const list=ENG.jobs.slice(0,6);
  if(!list.length){box.innerHTML='<p class="status">No jobs yet.</p>';return}
  box.innerHTML=list.map(j=>{
    const pct=Math.round((j.progress||0)*100),live=j.state==='running'||j.state==='queued';
    const badge={running:'Working',queued:'Waiting',done:'Done ✓',failed:'Problem',cancelled:'Cancelled',interrupted:'Interrupted'}[j.state]||j.state;
    const el=j.started?Math.round(((j.finished||Date.now()/1000)-j.started)):0;
    const eta=(j.state==='running'&&j.progress>0.03&&el>20)?` · about ${fmtDur(el/j.progress-el)} left`:'';
    return `<div class="eng-job ${j.state}"><div class="eng-job-top"><strong>${esc(JOB_NAMES[j.type]||j.type)}</strong><span class="badge ${j.state==='done'?'ok':j.state==='failed'||j.state==='interrupted'?'warn':''}">${badge}</span><span class="status">${el?fmtDur(el):''}${eta}</span></div>
      ${live||j.state==='cancelled'||j.state==='interrupted'?`<progress max="100" value="${pct}"></progress>`:''}
      <div class="status">${esc(j.message||'')}${live?` (${pct}%)`:''}</div>
      <div class="acts">${live?`<button class="btn small" data-jcancel="${j.id}">Cancel</button>`:''}${['cancelled','failed','interrupted'].includes(j.state)?`<button class="btn small" data-jresume="${j.id}">Resume</button>`:''}<a class="btn small" target="_blank" rel="noopener" href="${ENG.url}/api/jobs/${j.id}/log?t=${encodeURIComponent(ENG.token)}">Log</a></div></div>`}).join('');
}
function fmtDur(s){s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h?`${h} h ${m} min`:m?`${m} min ${s%60} s`:`${s} s`}
function engResolve(){for(const j of ENG.jobs){const w=ENG.waiters[j.id];if(w&&!['queued','running'].includes(j.state)){delete ENG.waiters[j.id];j.state==='done'?w.ok(j):w.fail(j)}}}
async function engRun(type,params){
  const j=await engFetch('/api/jobs',{method:'POST',body:{type,params}});
  return new Promise((ok,fail)=>{ENG.waiters[j.id]={ok:jj=>ok(jj.result),fail:jj=>fail(new Error(jj.state==='cancelled'?'Cancelled.':(jj.error||jj.message||'The job stopped.')))};engResolve()});
}
// a resumed job finishes under a new id: keep the screen in step with it
async function engAfterResume(job){
  if(job.type==='prepare')engWaitAndLoad(job.id);
  if(job.type==='render')engWaitRender(job.id);
}

/* ---------- parameters shared by sound / render ---------- */
function snd(){return {...SND_DEFAULTS,...(STU.settings.snd||{})}}
function engParams(extra){
  const sep=STU.settings.separate&&STU.engAudio;
  return {video:STU.engVideo,audio:sep?STU.engAudio:null,sync:sep?(STU.sync||null):null,sound:snd(),...extra};
}
function engNeed(sync){
  if(!STU.engVideo){alert('Choose your video first (step 1).');return false}
  if(STU.settings.separate&&!STU.engAudio){alert('Choose your separate audio, or untick “I recorded the audio separately”.');return false}
  if(sync&&STU.settings.separate&&!STU.sync){alert('Find the sync first (step 3).');return false}
  return true;
}

/* ---------- step 1: files, report, prepare ---------- */
async function engPick(kind){
  stuSay('#engVideoPath',kind==='video'?'A “Choose” window opened — it may be behind this browser window…':$('#engVideoPath').textContent);
  if(kind==='audio')stuSay('#engAudioPath','A “Choose” window opened — it may be behind this browser window…');
  try{const d=await engChoose(kind);if(d.path)engSetFile(kind,d.path)}catch(e){alert(e.message)}
  engShow();
}
function engSetFile(kind,path){
  if(kind==='video'){STU.engVideo=path;STU.videoName=baseName(path);STU.engPrep=null;STU.sync=null}else{STU.engAudio=path;STU.audioName=baseName(path);STU.engPrep=null;STU.sync=null}
  $('#engReport').innerHTML='';stuSave();stuRenderInfo();engShow();
}
async function engReport(){
  if(!engNeed())return;$('#engReport').innerHTML='<p class="status">Checking your files…</p>';
  try{const r=await engRun('report',{video:STU.engVideo,audio:STU.settings.separate?STU.engAudio:null});
    $('#engReport').innerHTML=`<div class="eng-report">${r.notes.map(n=>`<div class="eng-note ${n.level}"><span>${n.level==='warn'?'⚠':n.level==='info'?'ℹ':'✓'}</span><span>${esc(n.text)}</span></div>`).join('')}</div>`}
  catch(e){$('#engReport').innerHTML=`<p class="status">${esc(e.message)}</p>`}
}
async function engPrepare(){
  if(!engNeed())return;
  try{const r=await engRun('prepare',{video:STU.engVideo,audio:STU.settings.separate?STU.engAudio:null});await engLoadPrepared(r)}
  catch(e){stuSay('#stuPrepStatus',e.message)}
}
async function engWaitAndLoad(id){/* used after Resume */
  await new Promise(ok=>{ENG.waiters[id]={ok:j=>{engLoadPrepared(j.result).finally(ok)},fail:()=>ok()};engResolve()});
}
async function engLoadPrepared(r,quiet){
  STU.engPrep=r;if(!quiet)stuSay('#stuPrepStatus','Loading the preview and sound into the editor…');
  const get=async p=>{const x=await fetch(engFileUrl(p));if(!x.ok)throw new Error('Could not read '+baseName(p)+' from the engine — press Prepare again.');return x};
  if(r.video_wav)STU.wavV=parseWav(await (await get(r.video_wav)).arrayBuffer());
  if(r.audio_wav&&STU.settings.separate)STU.wavA=parseWav(await (await get(r.audio_wav)).arrayBuffer());
  if(r.info)STU.info=await (await get(r.info)).json();
  if(r.proxy){stuVideo().src=engFileUrl(r.proxy)}
  STU.dur=STU.wavV?STU.wavV.x.length/STU.wavV.rate:(r.duration||STU.dur);
  await stuSave();stuRenderInfo();if(!quiet)stuSay('#stuPrepStatus','Prepared ✓ — preview and sound are loaded. Next: find the sync (step 3).');
}

/* ---------- step 3: sync ---------- */
async function engSync(onStatus){
  if(!STU.engPrep||!STU.engPrep.audio_wav)throw new Error('Press “Prepare” in step 1 first.');
  onStatus('The engine is finding the sync…');
  const r=await engRun('sync',{video_wav:STU.engPrep.video_wav,audio_wav:STU.engPrep.audio_wav});
  return {a:r.a,b:r.b,conf:r.conf,engine:true,residual_ms:r.residual_ms,drift_ppm:r.drift_ppm,points:r.points,message:r.message};
}

/* ---------- step 4: sound ---------- */
function engSoundUi(){
  const s=snd();$('#snd_strength').value=s.strength;$('#snd_strengthVal').textContent=s.strength;$('#snd_lufs').value=String(s.lufs);
  ['hp','presence','deess','comp','roomtone'].forEach(k=>$('#snd_'+k).checked=!!s[k]);
}
function engSoundSave(){
  STU.settings.snd={strength:+$('#snd_strength').value,lufs:+$('#snd_lufs').value,...Object.fromEntries(['hp','presence','deess','comp','roomtone'].map(k=>[k,$('#snd_'+k).checked]))};
  $('#snd_strengthVal').textContent=STU.settings.snd.strength;stuSave();
}
async function engPreview(){
  if(!engNeed(true))return;const v=stuVideo();
  const start=v&&v.currentTime>1?v.currentTime:Math.max(0,(STU.dur||120)*0.25);
  stuSay('#engSoundStatus',`Making a 30-second preview from ${fmtT(start)}…`);$('#engPreviewBtn').disabled=true;
  try{const r=await engRun('preview',engParams({start}));
    $('#engBefore').src=engFileUrl(r.before);$('#engAfter').src=engFileUrl(r.after);$('#engAB').hidden=false;
    stuSay('#engSoundStatus',`Preview from ${fmtT(r.start)} ready. Play Before, then After. Change the settings and preview again until it sounds right.`)}
  catch(e){stuSay('#engSoundStatus',e.message)}finally{$('#engPreviewBtn').disabled=false}
}
async function engClean(){
  if(!engNeed(true))return;stuSay('#engSoundStatus','Cleaning the whole recording (see progress above)…');
  try{const r=await engRun('clean',engParams());STU.engClean=r.clean_audio||null;stuSave();if(typeof edAudioSrc==='function')edAudioSrc();stuSay('#engSoundStatus',r.message+(window.studioApp?' You can now hear it in the Editor (Sound: Recorder).':''))}catch(e){stuSay('#engSoundStatus',e.message)}
}

/* ---------- step 8: render + quality check ---------- */
function engRenderParams(){
  const keep=keepSegments(),hasText=STU.words.length>0;
  const reels=STU.reels.map((r,i)=>{const segs=clipSegs(keep,r.start,r.end);return segs.length?{name:`Reel ${String(i+1).padStart(2,'0')} - ${safeName(r.title)}`,start:r.start,end:r.end,segs,ass:STU.settings.reelCaps&&hasText?picReelAss(segs):''}:null}).filter(Boolean);
  return engParams({keep,reels,srt:hasText?makeSrt(keep):'',settings:{res:STU.settings.res,enc:STU.settings.enc,reelRes:STU.settings.reelRes,reelCaps:STU.settings.reelCaps,xfade_ms:STU.settings.xfade??40,...picRenderSettings()},covers:picCovers(),youtube:typeof ytFullText==='function'?ytFullText():'',chapters:typeof chaptersText==='function'?chaptersText():''});
}
async function engRender(){
  if(!engNeed(true))return;
  if(!STU.dur){alert('Press “Prepare” in step 1 first.');return}
  $('#engQC').hidden=true;$('#engOpenFolder').hidden=true;$('#engRenderBtn').disabled=true;
  try{const r=await engRun('render',engRenderParams());engShowQC(r)}catch(e){$('#engQC').textContent=e.message;$('#engQC').hidden=false}
  finally{$('#engRenderBtn').disabled=false}
}
async function engWaitRender(id){await new Promise(ok=>{ENG.waiters[id]={ok:j=>{engShowQC(j.result);ok()},fail:()=>ok()};engResolve()})}
function engShowQC(r){
  $('#engQC').textContent=`${r.message}\n\n${r.qc.text}\n\nFiles:\n${r.outputs.map(baseName).join('\n')}`;$('#engQC').hidden=false;
  const b=$('#engOpenFolder');b.hidden=false;b.onclick=()=>(window.studioApp&&window.studioApp.showItem?window.studioApp.showItem(r.outputs[0]):engFetch('/api/open-folder',{method:'POST',body:{path:r.outputs[0]}})).catch(e=>alert(e.message));
}

function engInit(){
  ENG.token=engToken();
  STU.settings.snd={...SND_DEFAULTS,...(STU.settings.snd||{})};engSoundUi();engShow();
  $('#engRetry').addEventListener('click',()=>{ENG.token=engToken();engSay('Looking for the Studio Engine…');engCheck()});
  $('#engHwTest').addEventListener('click',async()=>{$('#engHw').textContent='Testing… (about 20 seconds)';try{ENG.hw=await engFetch('/api/hardware?force=1');engHwRender()}catch(e){$('#engHw').textContent=e.message}});
  document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>{const t=$('#'+b.dataset.copy).textContent;navigator.clipboard.writeText(t).then(()=>{b.textContent='Copied ✓';setTimeout(()=>b.textContent='Copy',2000)}).catch(()=>prompt('Copy this line:',t))}));
  $('#engPickVideo').addEventListener('click',()=>engPick('video'));$('#engPickAudio').addEventListener('click',()=>engPick('audio'));
  document.querySelectorAll('[data-paste]').forEach(b=>b.addEventListener('click',async()=>{const raw=$('#engPastePath').value,p=raw.trim().replace(/^"|"$/g,'');if(!p)return;
    try{const d=await engFetch('/api/check-path',{method:'POST',body:{path:p}});if(!d.exists){alert('No file found at that location. Check the path and try again.');return}engSetFile(b.dataset.paste,d.path);if($('#engPastePath').value===raw)$('#engPastePath').value=''}catch(e){alert(e.message)}}));
  $('#engReportBtn').addEventListener('click',engReport);$('#engPrepBtn').addEventListener('click',engPrepare);
  ['snd_strength','snd_lufs','snd_hp','snd_presence','snd_deess','snd_comp','snd_roomtone'].forEach(id=>$('#'+id).addEventListener(id==='snd_strength'?'input':'change',engSoundSave));
  $('#engPreviewBtn').addEventListener('click',engPreview);$('#engCleanBtn').addEventListener('click',engClean);
  $('#engRenderBtn').addEventListener('click',engRender);
  $('#engJobs').addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;
    try{if(b.dataset.jcancel)await engFetch(`/api/jobs/${b.dataset.jcancel}/cancel`,{method:'POST'});
      if(b.dataset.jresume){const j=await engFetch(`/api/jobs/${b.dataset.jresume}/resume`,{method:'POST'});engAfterResume(j)}}catch(err){alert(err.message)}});
  $('#stu_separate').addEventListener('change',engShow);
  // only knock on the engine's door when it is expected (installed before, or just opened by it)
  if(ENG.token)engCheck();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&ENG.token&&!ENG.on)engCheck()});
  // the engine's pairing link can arrive in a tab that is already open (only the #engine=… part changes)
  window.addEventListener('hashchange',()=>{if(/engine=/.test(location.hash)){ENG.token=engToken();if(!ENG.on)engCheck()}});
}
