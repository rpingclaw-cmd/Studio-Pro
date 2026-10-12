/* =====================================================================
   STUDIO · EDITOR — a DaVinci Resolve-style editing room
   Dark full-screen layout: viewer in the middle, panels left and right, the timeline below, and the
   pages along the bottom (Media · Edit · Color · Fairlight · Deliver). It edits the SAME project as the
   guided steps: while it is open the guided controls are moved into its panels, and moved back on close.
   Hand edits on the timeline are kept as STU.manualCuts (source seconds, used by keepSegments) and
   STU.splits (blade points, for selecting part of a clip). Undo/redo covers every change to the project.
   ===================================================================== */
const ED={open:false,page:'edit',pps:0,scroll:0,sel:null,inT:null,outT:null,tool:'select',snap:true,
  undo:[],redo:[],last:null,lastAt:0,quiet:false,holders:[],dirty:true,model:null,shuttle:0,drag:null,
  peaks:null,peaksOf:null,raf:0,lastTs:0,drawKey:'',source:false,scopeAt:0,scopeMode:'wave',hover:null};
const ED_PAGES={
  media:{label:'Media',left:['#edPool','#stuStep1','#stuStep2'],right:['#engBox','#stuStep3'],tl:false},
  edit:{label:'Edit',left:['#stuStep6','#stuStep5'],right:['#edInspector','#stuStep7'],tl:true},
  color:{label:'Color',left:['#edScopes'],right:['#stuPicture'],tl:true},
  fairlight:{label:'Fairlight',left:['#stuSound'],right:['#edMeters'],tl:true,tall:true},
  deliver:{label:'Deliver',left:['#stuStep10'],right:['#engBox','#stuYouTube'],tl:true},
};
const ED_ICONS={media:'M3 5h18v14H3zM3 9h18M8 5v4M13 5v4',edit:'M3 6h18M3 12h12M3 18h16M17 10v4',
  color:'M12 3a9 9 0 1 0 0 18a4 4 0 0 1 0-8a4 4 0 0 0 0-10z',fairlight:'M4 14v-4M8 18V6M12 15V9M16 20V4M20 13v-2',
  deliver:'M12 3v12M7 10l5 5l5-5M4 21h16'};
const ED_TRACKS=[{id:'V2',name:'V2',sub:'Covers',h:26},{id:'V1',name:'V1',sub:'Video',h:44},{id:'A1',name:'A1',sub:'Voice',h:58},{id:'ST',name:'ST',sub:'Subtitles',h:24}];
const ED_HEAD=74,ED_RULER=30;

/* ---------- time helpers ---------- */
const edFps=()=>{try{return +videoFps()||30}catch(e){return 30}};
function edTc(t){const f=edFps(),fr=Math.round(Math.max(0,t)*f),r=Math.round(f),ff=fr%r,s=Math.floor(fr/r),p=n=>String(n).padStart(2,'0');
  return `${p(Math.floor(s/3600))}:${p(Math.floor(s%3600/60))}:${p(s%60)}:${p(ff)}`}
function edModel(){
  if(!ED.dirty&&ED.model)return ED.model;
  const keep=keepSegments(),m=mapper(keep),splits=[...new Set(STU.splits||[])].sort((a,b)=>a-b),clips=[];let o=0;
  keep.forEach(([s,e],k)=>{let a=s;for(const x of splits){if(x>a+0.05&&x<e-0.05){clips.push({s:a,e:x,o,part:k});o+=x-a;a=x}}clips.push({s:a,e,o,part:k});o+=e-a});
  const range=(a,b)=>{let lo=null,hi=null;for(const c of clips){const x=Math.max(c.s,a),y=Math.min(c.e,b);if(y>x){if(lo==null)lo=c.o+(x-c.s);hi=c.o+(y-c.s)}}return lo==null?null:{a:lo,b:hi}};
  let caps=[];try{caps=STU.words.length?captionChunks(keep,10,60):[]}catch(e){}
  const covers=(STU.covers||[]).map((c,i)=>{const r=range(+c.start,+c.end);return r&&{...r,i,c}}).filter(Boolean);
  const marks=(STU.chapters||[]).map((c,i)=>({t:chapterEditedTime(c,keep,m),i,c})).filter(x=>x.t!=null);
  const reels=(STU.reels||[]).map((r,i)=>{const x=range(+r.start,+r.end);return x&&{...x,i,r}}).filter(Boolean);
  ED.model={keep,clips,total:o,caps,covers,marks,reels,range};ED.dirty=false;
  if(!ED.userZoom&&ED.fitTotal!=null&&Math.abs(o-ED.fitTotal)>Math.max(1,ED.fitTotal*0.2))ED.needFit=true;
  return ED.model;
}
function edToSrc(t){const M=edModel();for(const c of M.clips)if(t>=c.o-1e-6&&t<=c.o+(c.e-c.s)+1e-6)return c.s+(t-c.o);
  const l=M.clips[M.clips.length-1];return l?(t<=0?M.clips[0].s:l.e):Math.max(0,t)}
function edToEd(src){const M=edModel();for(const c of M.clips){if(src<c.s-1e-6)return c.o;if(src<=c.e+1e-6)return c.o+(src-c.s)}return M.total}
function edNow(){const v=stuVideo();return v?edToEd(v.currentTime||0):0}
function edSeek(t){const v=stuVideo(),M=edModel();t=Math.max(0,Math.min(M.total,t));if(v)v.currentTime=edToSrc(t);ED.drawKey=''}
function edEditPoints(){const M=edModel(),pts=new Set([0,M.total]);M.clips.forEach(c=>{pts.add(+c.o.toFixed(4))});M.covers.forEach(c=>{pts.add(c.a);pts.add(c.b)});M.marks.forEach(x=>pts.add(x.t));return [...pts].sort((a,b)=>a-b)}
function edClipAt(t){const M=edModel();return M.clips.find(c=>t>=c.o&&t<c.o+(c.e-c.s))||null}

/* ---------- undo / redo: a snapshot of the project after every save ---------- */
function edSnap(){return JSON.stringify({removed:STU.removed||{},manualCuts:STU.manualCuts||[],splits:STU.splits||[],covers:STU.covers||[],frames:STU.frames||[],chapters:STU.chapters||[],reels:STU.reels||[],settings:STU.settings})}
function edRecord(){
  if(ED.quiet)return;const s=edSnap();if(ED.last===null){ED.last=s;return}if(s===ED.last)return;
  const now=Date.now(),el=document.activeElement,typing=el&&/^(INPUT|TEXTAREA)$/.test(el.tagName)&&el.type!=='checkbox';
  if(!(typing&&el===ED.lastEl&&now-ED.lastAt<1500&&ED.undo.length)){ED.undo.push(ED.last);if(ED.undo.length>200)ED.undo.shift()}  // typing in one box = one step
  ED.lastAt=now;ED.lastEl=typing?el:null;ED.last=s;ED.redo=[];edButtons();
}
function edApply(s){
  const d=JSON.parse(s);ED.quiet=true;
  Object.assign(STU,{removed:d.removed,manualCuts:d.manualCuts,splits:d.splits,covers:d.covers,frames:d.frames,chapters:d.chapters,reels:d.reels,settings:d.settings});
  ED.last=s;ED.lastAt=0;try{stuSave()}finally{ED.quiet=false}
  for(const f of [stuRenderInfo,stuRenderTranscript,()=>stuRenderChapters(),()=>picUi(),()=>picFramesRender()])try{f()}catch(e){}
  ED.sel=null;edRefresh();
}
function edUndo(){if(!ED.undo.length)return edSay('Nothing to undo.');ED.redo.push(edSnap());edApply(ED.undo.pop());edSay('Undone.')}
function edRedo(){if(!ED.redo.length)return edSay('Nothing to redo.');ED.undo.push(edSnap());edApply(ED.redo.pop());edSay('Redone.')}
function edButtons(){const u=$('#edUndo'),r=$('#edRedo');if(u)u.disabled=!ED.undo.length;if(r)r.disabled=!ED.redo.length}
function edSay(t){const el=$('#edMsg');if(el)el.textContent=t||''}
function edChanged(msg){ED.dirty=true;stuSave();try{stuRenderTranscript()}catch(e){}try{stuRenderChapters()}catch(e){}edRefresh();if(msg)edSay(msg)}

/* ---------- editing actions ---------- */
function edBladeAt(src){const M=edModel(),c=M.clips.find(c=>src>c.s+0.05&&src<c.e-0.05);if(!c){edSay('Put the playhead inside a clip to cut it.');return false}
  STU.splits=[...(STU.splits||[]),+src.toFixed(3)];edChanged(`Clip cut at ${edTc(edToEd(src))}.`);return true}
function edCutSrc(a,b,msg){if(!(b-a>0.04))return;STU.manualCuts=[...(STU.manualCuts||[]),[+a.toFixed(3),+b.toFixed(3)]];ED.sel=null;edChanged(msg)}
function edDelete(){
  const s=ED.sel,ws=window.getSelection(),r=ws&&!ws.isCollapsed&&$('#stuText').contains(ws.anchorNode)?selectedRange():null;
  if(r){for(let i=r[0];i<=r[1];i++)STU.removed[i]='user';ws.removeAllRanges();edChanged(`${r[1]-r[0]+1} word${r[1]>r[0]?'s':''} cut.`);return}
  if(s&&s.type==='clip'){const c=edModel().clips.find(c=>Math.abs(c.s-s.s)<1e-3);if(c){const at=c.o;edCutSrc(c.s,c.e,`Clip removed (${(c.e-c.s).toFixed(1)} s) — the rest moved up to close the gap.`);edSeek(at)}return}
  if(s&&s.type==='cover'){STU.covers.splice(s.i,1);ED.sel=null;edChanged('Cover removed.');try{picCoversRender()}catch(e){}return}
  if(s&&s.type==='mark'){STU.chapters.splice(s.i,1);ED.sel=null;edChanged('Marker removed.');return}
  if(ED.inT!=null&&ED.outT!=null&&ED.outT>ED.inT){const a=edToSrc(ED.inT),b=edToSrc(ED.outT),at=ED.inT;ED.inT=ED.outT=null;edCutSrc(a,b,'In–Out range removed.');edSeek(at);return}
  edSay('Select a clip (click it), or mark In (I) and Out (O), then press Delete.');
}
/* drag a clip edge: shorter = cut by hand; longer = take back hand cuts and cut words in that stretch */
function edTrim(c,edge,src){
  if(edge==='e'){src=Math.max(c.s+0.2,src);if(src<c.e-0.02)return edCutSrc(src,c.e,'Clip end trimmed.');if(src>c.e+0.02)return edRestore(c.e,src)}
  else{src=Math.min(c.e-0.2,src);if(src>c.s+0.02)return edCutSrc(c.s,src,'Clip start trimmed.');if(src<c.s-0.02)return edRestore(src,c.s)}
}
function edRestore(a,b){
  STU.manualCuts=(STU.manualCuts||[]).flatMap(([x,y])=>y<=a||x>=b?[[x,y]]:[...(x<a?[[x,a]]:[]),...(y>b?[[b,y]]:[])]);
  let n=0;STU.words.forEach((w,i)=>{if(STU.removed[i]&&w.s>=a-0.05&&w.e<=b+0.05){delete STU.removed[i];n++}});
  edChanged(n?`Extended: ${n} word${n>1?'s':''} brought back.`:'Extended.');
}
function edMarker(){const v=stuVideo(),t=v?v.currentTime:0;STU.chapters=[...(STU.chapters||[]),{t,title:'Chapter'}].sort((a,b)=>a.t-b.t);
  const i=STU.chapters.findIndex(c=>c.t===t);ED.sel={type:'mark',i};edChanged('Marker added (it becomes a YouTube chapter). Name it in the Inspector.')}
function edFrameFor(c){return (STU.frames||[]).find(f=>Math.abs(f.start-c.s)<0.01&&Math.abs(f.end-c.e)<0.01)}

/* ---------- moving the guided controls in and out ---------- */
function edPlace(sel,box){const n=$(sel);if(!n||n.parentElement===box)return;
  if(!n.dataset.edHome&&!n.closest('#ed')){const ph=document.createComment('ed:'+sel);n.parentNode.insertBefore(ph,n);ED.holders.push([ph,n]);n.dataset.edHome='1'}
  box.appendChild(n)}
function edRestoreHome(){for(const [ph,n] of ED.holders.splice(0).reverse()){ph.parentNode.insertBefore(n,ph);ph.remove();delete n.dataset.edHome}}
function edShowPage(p){
  if(!ED_PAGES[p])p='edit';ED.page=p;try{localStorage.setItem('edPage',p)}catch(e){}
  const P=ED_PAGES[p],L=$('#edLeft'),R=$('#edRight'),S=$('#edStash');
  [...L.children,...R.children].forEach(n=>S.appendChild(n));
  P.left.forEach(s=>edPlace(s,L));P.right.forEach(s=>edPlace(s,R));
  $('#edBody').classList.toggle('notl',!P.tl);$('#edBody').classList.toggle('tall',!!P.tall);
  document.querySelectorAll('#ed .ed-pages button').forEach(b=>b.setAttribute('aria-selected',b.dataset.edpage===p?'true':'false'));
  $('#edVName').textContent=p==='media'?'Source viewer':p==='color'?'Color viewer — grade preview':'Timeline viewer';
  edGrade();edRefresh();
  if(P.tl&&(!ED.pps||ED.needFit))requestAnimationFrame(()=>{if(edW()>ED_HEAD+100){edFit();ED.needFit=false}});
}
function edOpen(){
  edBuild();if(ED.open)return;ED.open=true;$('#ed').hidden=false;document.documentElement.classList.add('ed-on');
  const v=stuVideo();edPlace('#stuVideo',$('#edScreen'));v.controls=false;
  let p='edit';try{p=localStorage.getItem('edPage')||'edit'}catch(e){}
  ED.dirty=true;if(!ED.pps)ED.needFit=true;edShowPage(p);edButtons();cancelAnimationFrame(ED.raf);ED.raf=requestAnimationFrame(edTick);
}
function edClose(){
  if(!ED.open)return;ED.open=false;cancelAnimationFrame(ED.raf);ED.shuttle=0;
  const v=stuVideo();v.style.filter='';v.controls=true;edRestoreHome();
  $('#ed').hidden=true;document.documentElement.classList.remove('ed-on');
}
function edRefresh(){ED.dirty=true;ED.drawKey='';if(!ED.open)return;edInspect();edPool();edMeters();edButtons();if(ED.pps)edZoomUi()}

/* ---------- building the editor ---------- */
function edBuild(){
  if($('#ed'))return;
  const ico=k=>`<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${ED_ICONS[k]}"/></svg>`;
  const d=document.createElement('div');d.id='ed';d.className='ed';d.hidden=true;
  d.innerHTML=`<div class="ed-top"><span class="ed-brand">◆ Studio Pro</span><span class="ed-proj" id="edProj"></span><span class="spacer"></span>
    <span class="ed-msg" id="edMsg"></span>
    <button class="ed-b" id="edUndo" title="Undo (Ctrl+Z)">↶ Undo</button><button class="ed-b" id="edRedo" title="Redo (Ctrl+Shift+Z)">↷ Redo</button>
    <button class="ed-b" id="edHelpBtn" title="Keyboard shortcuts (?)">⌨ Keys</button><button class="ed-b primary" id="edClose" title="Back to the step-by-step page">Guided view</button></div>
  <div class="ed-body" id="edBody">
    <aside class="ed-panel ed-left" id="edLeft"></aside>
    <section class="ed-viewer"><div class="ed-vhead"><span id="edVName">Timeline viewer</span><span class="spacer"></span>
        <button class="ed-b sm" id="edSrcBtn" title="Timeline: plays the edit and skips the cuts. Source: plays the whole recording.">Plays: edited timeline</button></div>
      <div class="ed-screen" id="edScreen"></div>
      <div class="ed-transport"><span class="ed-tc" id="edTc">00:00:00:00</span>
        <button class="ed-t" data-ed="start" title="Go to start (Home)">⏮</button><button class="ed-t" data-ed="prev" title="Previous edit (Up arrow)">⇤</button>
        <button class="ed-t" data-ed="rev" title="Play backwards (J)">◀◀</button><button class="ed-t" data-ed="stop" title="Stop (K)">■</button>
        <button class="ed-t big" data-ed="play" id="edPlay" title="Play / pause (Space)">▶</button><button class="ed-t" data-ed="fwd" title="Play faster (L, press again for 2× / 4×)">▶▶</button>
        <button class="ed-t" data-ed="next" title="Next edit (Down arrow)">⇥</button><button class="ed-t" data-ed="end" title="Go to end (End)">⏭</button>
        <button class="ed-t" data-ed="in" title="Mark In (I)">[ I</button><button class="ed-t" data-ed="out" title="Mark Out (O)">O ]</button>
        <span class="ed-tc dim" id="edDur"></span></div></section>
    <aside class="ed-panel ed-right" id="edRight"></aside>
    <section class="ed-tl" id="edTl"><div class="ed-tlbar">
        <button class="ed-b sm on" data-tool="select" title="Selection tool (A)">↖ Select</button><button class="ed-b sm" data-tool="blade" title="Blade tool (B): click a clip to cut it">✂ Blade</button>
        <button class="ed-b sm on" id="edSnapBtn" title="Snapping (N)">🧲 Snap</button><span class="ed-sep"></span>
        <button class="ed-b sm" data-ed="split" title="Cut the clip at the playhead (Ctrl+B)">Cut at playhead</button>
        <button class="ed-b sm" data-ed="del" title="Remove the selected clip or the In–Out range and close the gap (Delete)">Ripple delete</button>
        <button class="ed-b sm" data-ed="mark" title="Add a marker / chapter at the playhead (M)">◆ Marker</button>
        <button class="ed-b sm" data-ed="cover" title="Cover the selected clip or In–Out range with other pictures (sound continues)">▣ Cover</button>
        <button class="ed-b sm" data-ed="reel" title="Make a Reel from the In–Out range">＋ Reel</button><span class="spacer"></span>
        <span class="ed-zoomlbl">Zoom</span><button class="ed-b sm" data-ed="zout" title="Zoom out (Ctrl + minus)">−</button><input type="range" id="edZoom" min="0" max="100" value="0" aria-label="Timeline zoom"><button class="ed-b sm" data-ed="zin" title="Zoom in (Ctrl + plus)">+</button><button class="ed-b sm" data-ed="fit" title="Fit the whole timeline (Shift+Z)">Fit</button></div>
      <div class="ed-tlwrap" id="edTlWrap"><canvas id="edCanvas" tabindex="0" aria-label="Timeline"></canvas></div>
      <input type="range" id="edScroll" min="0" max="1000" value="0" aria-label="Scroll the timeline"></section>
  </div>
  <nav class="ed-pages">${Object.keys(ED_PAGES).map(k=>`<button data-edpage="${k}" aria-selected="false">${ico(k)}<span>${ED_PAGES[k].label}</span></button>`).join('')}</nav>
  <div id="edStash" hidden>
    <fieldset id="edPool"><legend>Media pool</legend><div id="edPoolList"></div></fieldset>
    <fieldset id="edInspector"><legend>Inspector</legend><div id="edInsp"></div></fieldset>
    <fieldset id="edScopes"><legend>Scopes</legend><div class="ed-scopebar"><button class="ed-b sm on" data-scope="wave">Waveform</button><button class="ed-b sm" data-scope="parade">RGB parade</button></div>
      <canvas id="edScope" width="384" height="216"></canvas><p class="hint" id="edScopeNote" style="margin:6px 0 0">Brightness of the picture under the playhead: 0 at the bottom, 100 at the top. Faces usually sit around 60–70.</p>
      <p class="hint" style="margin:8px 0 0">The viewer shows a quick preview of your colour settings. For the exact picture press “Show a still” in the Picture panel.</p></fieldset>
    <fieldset id="edMeters"><legend>Sound</legend><div id="edMeterInfo"></div></fieldset>
  </div>
  <div class="ed-help" id="edHelp" hidden><div class="ed-helpbox"><h3>Keyboard shortcuts</h3><div class="ed-keys">
    <b>Space</b><span>Play / pause</span><b>J · K · L</b><span>Backwards · stop · forwards (L again = faster)</span>
    <b>← →</b><span>One frame (Shift: one second)</span><b>↑ ↓</b><span>Previous / next edit point</span><b>Home · End</b><span>Start / end</span>
    <b>I · O</b><span>Mark In / Out (Alt+X clears)</span><b>Ctrl+B</b><span>Cut the clip at the playhead</span><b>A · B</b><span>Selection tool / Blade tool</span>
    <b>Delete</b><span>Ripple delete the selected clip or In–Out range</span><b>M</b><span>Add a marker (chapter)</span><b>N</b><span>Snapping on/off</span>
    <b>Ctrl+Z · Ctrl+Shift+Z</b><span>Undo / redo</span><b>Ctrl + / −, Shift+Z</b><span>Zoom in / out, fit</span><b>Ctrl + wheel</b><span>Zoom at the mouse</span>
    <b>Shift+1…5</b><span>Media · Edit · Color · Fairlight · Deliver</span><b>Esc</b><span>Clear selection / close this</span></div>
    <p class="hint" style="margin:10px 0 0">Drag the edge of a clip to trim it. Drag in the ruler to scrub. Click words in the transcript to jump there.</p>
    <button class="ed-b primary" id="edHelpClose">Close</button></div></div>`;
  document.body.appendChild(d);
  $('#edClose').addEventListener('click',edClose);$('#edUndo').addEventListener('click',edUndo);$('#edRedo').addEventListener('click',edRedo);
  $('#edHelpBtn').addEventListener('click',()=>{$('#edHelp').hidden=false});$('#edHelpClose').addEventListener('click',()=>{$('#edHelp').hidden=true});
  d.querySelector('.ed-pages').addEventListener('click',e=>{const b=e.target.closest('[data-edpage]');if(b)edShowPage(b.dataset.edpage)});
  d.addEventListener('click',e=>{const b=e.target.closest('[data-ed]');if(b)edAct(b.dataset.ed);const t=e.target.closest('[data-tool]');if(t)edTool(t.dataset.tool);
    const sc=e.target.closest('[data-scope]');if(sc){ED.scopeMode=sc.dataset.scope;document.querySelectorAll('[data-scope]').forEach(x=>x.classList.toggle('on',x===sc));ED.scopeAt=0}});
  $('#edSnapBtn').addEventListener('click',()=>{ED.snap=!ED.snap;$('#edSnapBtn').classList.toggle('on',ED.snap)});
  $('#edSrcBtn').addEventListener('click',()=>{ED.source=!ED.source;$('#edSrcBtn').textContent=ED.source?'Plays: whole source':'Plays: edited timeline'});
  $('#edZoom').addEventListener('input',e=>{const [lo,hi]=edZoomRange();edZoomTo(lo*Math.pow(hi/lo,+e.target.value/100),null)});
  $('#edScroll').addEventListener('input',e=>{ED.scroll=+e.target.value;ED.drawKey=''});
  $('#edScreen').addEventListener('click',()=>edAct('play'));
  edCanvasEvents();
  $('#edInsp').addEventListener('change',edInspChange);$('#edInsp').addEventListener('input',e=>{if(e.target.dataset.live)edInspChange(e)});
  $('#edInsp').addEventListener('click',e=>{const b=e.target.closest('[data-ins]');if(b)edInspAct(b.dataset.ins)});
  new ResizeObserver(()=>{ED.drawKey=''}).observe($('#edTlWrap'));
}
function edTool(t){ED.tool=t;document.querySelectorAll('#ed [data-tool]').forEach(b=>b.classList.toggle('on',b.dataset.tool===t));$('#edCanvas').style.cursor=t==='blade'?'crosshair':'default'}
function edAct(a){
  const v=stuVideo(),M=edModel(),now=edNow();
  if(a==='play'){if(!v.src)return edSay('Load your video first (Media page).');ED.shuttle=0;v.playbackRate=1;if(v.paused)v.play().catch(()=>edSay('This browser cannot play the preview — use Chrome or Edge.'));else v.pause()}
  else if(a==='stop'){ED.shuttle=0;v.pause();v.playbackRate=1}
  else if(a==='fwd'){if(!v.src)return;const r=ED.shuttle>0?Math.min(4,ED.shuttle*2):1;ED.shuttle=r;v.playbackRate=r;v.play().catch(()=>{});edSay(r>1?`Playing ${r}×`:'')}
  else if(a==='rev'){if(!v.src)return;v.pause();ED.shuttle=ED.shuttle<0?Math.max(-4,ED.shuttle*2):-1;edSay(`Playing backwards ${-ED.shuttle}×`)}
  else if(a==='start')edSeek(0);else if(a==='end')edSeek(M.total);
  else if(a==='prev'){const p=edEditPoints().filter(x=>x<now-0.02);edSeek(p.length?p[p.length-1]:0)}
  else if(a==='next'){const p=edEditPoints().find(x=>x>now+0.02);edSeek(p!=null?p:M.total)}
  else if(a==='in'){ED.inT=now;if(ED.outT!=null&&ED.outT<=now)ED.outT=null;edSay('In marked.')}
  else if(a==='out'){ED.outT=now;if(ED.inT!=null&&ED.inT>=now)ED.inT=null;edSay('Out marked.')}
  else if(a==='split')edBladeAt(v.currentTime);else if(a==='del')edDelete();else if(a==='mark')edMarker();
  else if(a==='cover'){const r=edSelRange();if(!r)return edSay('Select a clip, or mark In and Out, to choose what to cover.');
    STU.covers=[...(STU.covers||[]),{start:+r[0].toFixed(2),end:+r[1].toFixed(2),mode:'auto'}];ED.sel={type:'cover',i:STU.covers.length-1};edChanged('Cover added on V2. Choose what it shows in the Inspector.');try{picCoversRender()}catch(e){}}
  else if(a==='reel'){const r=edSelRange();if(!r)return edSay('Mark In and Out (or select a clip) for the Reel.');
    STU.reels=[...(STU.reels||[]),{title:'Reel '+((STU.reels||[]).length+1),start:+r[0].toFixed(2),end:+r[1].toFixed(2)}];edChanged('Reel added (see Reels & chapters).');try{stuStats()}catch(e){}}
  else if(a==='zin')edZoomTo(ED.pps*1.5,null);else if(a==='zout')edZoomTo(ED.pps/1.5,null);else if(a==='fit')edFit();
  ED.drawKey='';
}
/* the selection as a source-time range (clip, or In–Out) */
function edSelRange(){const s=ED.sel;if(s&&s.type==='clip')return [s.s,s.e];if(ED.inT!=null&&ED.outT!=null&&ED.outT>ED.inT)return [edToSrc(ED.inT),edToSrc(ED.outT)];return null}

/* ---------- zoom & scroll ---------- */
function edW(){const c=$('#edCanvas');return c?c.clientWidth:800}
function edZoomRange(){const M=edModel(),fit=(edW()-ED_HEAD-24)/Math.max(1,M.total);return [Math.max(0.005,fit*0.5),400]}
function edFit(){const M=edModel();ED.pps=Math.max(0.005,(edW()-ED_HEAD-24)/Math.max(1,M.total));ED.scroll=0;ED.fitTotal=M.total;ED.userZoom=false;edZoomUi();ED.drawKey=''}
function edZoomTo(p,atX){ED.userZoom=true;const [lo,hi]=edZoomRange();p=Math.min(hi,Math.max(lo,p));const ax=atX??(edX(edNow())),t=(ax-ED_HEAD+ED.scroll)/ED.pps;
  ED.pps=p;ED.scroll=Math.max(0,t*p-(ax-ED_HEAD));edZoomUi();ED.drawKey=''}
function edZoomUi(){const [lo,hi]=edZoomRange(),z=$('#edZoom');if(z)z.value=String(Math.round(100*Math.log(ED.pps/lo)/Math.log(hi/lo)));
  const s=$('#edScroll'),max=Math.max(0,edModel().total*ED.pps-(edW()-ED_HEAD-24));if(s){s.max=String(Math.ceil(max));s.value=String(ED.scroll);s.disabled=max<=0}ED.scroll=Math.min(ED.scroll,max)}
const edX=t=>ED_HEAD+t*ED.pps-ED.scroll;
const edT=x=>(x-ED_HEAD+ED.scroll)/ED.pps;

/* ---------- playback loop ---------- */
function edTick(ts){
  if(!ED.open)return;const v=stuVideo(),dt=ED.lastTs?Math.min(0.1,(ts-ED.lastTs)/1000):0;ED.lastTs=ts;
  if(v&&v.src){
    const M=edModel();
    if(ED.shuttle<0){let t=v.currentTime+ED.shuttle*dt;if(!ED.source){const c=M.clips.find(c=>t>=c.s&&t<=c.e);if(!c){const pr=[...M.clips].reverse().find(c=>c.e<=t+1e-6);t=pr?pr.e-0.01:0}}
      if(t<=0){t=0;ED.shuttle=0}v.currentTime=t}
    else if(!ED.source&&!v.paused&&M.keep.length){const t=v.currentTime;if(!M.keep.some(([s,e])=>t>=s-0.03&&t<e)){const nx=M.keep.find(([s])=>s>t);if(nx)v.currentTime=nx[0];else v.pause()}}
    $('#edPlay').textContent=v.paused&&ED.shuttle>=0?'▶':'❚❚';
  }
  const now=edNow();$('#edTc').textContent=edTc(now);$('#edDur').textContent='/ '+edTc(edModel().total);
  if(v&&!v.paused&&ED.pps){const x=edX(now),w=edW();if(x>w-30||x<ED_HEAD){ED.scroll=Math.max(0,now*ED.pps-(w-ED_HEAD)*0.25);edZoomUi()}}
  if(ED.needFit&&ED_PAGES[ED.page].tl&&edW()>ED_HEAD+100){ED.needFit=false;edFit()}
  edDraw();
  if(ED.page==='color'&&ts-ED.scopeAt>250){ED.scopeAt=ts;edScope()}
  ED.raf=requestAnimationFrame(edTick);
}

/* ---------- the timeline drawing ---------- */
function edPeaks(){
  const src=(typeof ANA!=='undefined'&&ANA)?ANA:STU.wavV;if(!src)return null;if(ED.peaksOf===src)return ED.peaks;
  let p;if(src===STU.wavV){const n=Math.ceil(src.x.length/src.rate*100),hop=src.rate/100;p=new Float32Array(n);
    for(let i=0;i<n;i++){let m=0;const a=Math.floor(i*hop),b=Math.min(src.x.length,Math.floor((i+1)*hop));for(let j=a;j<b;j++){const y=Math.abs(src.x[j]);if(y>m)m=y}p[i]=m}}
  else{p=new Float32Array(src.env.length);for(let i=0;i<p.length;i++)p[i]=Math.pow(10,(src.env[i]-100)/20)}
  ED.peaks=p;ED.peaksOf=src;return p;
}
function edTracks(){return ED_TRACKS.map(t=>t.id==='A1'&&ED_PAGES[ED.page].tall?{...t,h:120}:t)}
function edDraw(){
  const c=$('#edCanvas');if(!c||!ED_PAGES[ED.page].tl)return;const w=c.clientWidth,h=c.clientHeight;if(!w||!h)return;
  const v=stuVideo(),now=edNow(),M=edModel();
  const key=[w,h,ED.pps.toFixed(5),ED.scroll.toFixed(1),now.toFixed(3),JSON.stringify(ED.sel),ED.inT,ED.outT,ED.dirty,ED.page,ED.hover,ED.drag&&ED.drag.x].join('|');
  if(key===ED.drawKey)return;ED.drawKey=key;
  const dpr=window.devicePixelRatio||1;if(c.width!==Math.round(w*dpr)||c.height!==Math.round(h*dpr)){c.width=Math.round(w*dpr);c.height=Math.round(h*dpr)}
  const g=c.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,w,h);g.fillStyle='#1e1e21';g.fillRect(0,0,w,h);
  g.font='11px system-ui,Segoe UI,sans-serif';g.textBaseline='middle';
  const t0=Math.max(0,edT(ED_HEAD)),t1=edT(w);
  // ruler
  g.fillStyle='#2a2a2e';g.fillRect(ED_HEAD,0,w-ED_HEAD,ED_RULER);
  const steps=[1/edFps(),0.5,1,2,5,10,15,30,60,120,300,600,900,1800,3600];const step=steps.find(s=>s*ED.pps>=90)||3600;
  g.strokeStyle='#55555d';g.fillStyle='#9a9aa4';g.beginPath();
  for(let t=Math.floor(t0/step)*step;t<=t1;t+=step){const x=Math.round(edX(t))+0.5;if(x<ED_HEAD)continue;g.moveTo(x,ED_RULER-10);g.lineTo(x,ED_RULER);g.fillText(edTc(t).replace(/^00:/,''),x+3,9);
    for(let k=1;k<5;k++){const xx=Math.round(edX(t+k*step/5))+0.5;g.moveTo(xx,ED_RULER-4);g.lineTo(xx,ED_RULER)}}g.stroke();
  // reels (thin bars) and in/out on the ruler
  M.reels.forEach(r=>{const a=edX(r.a),b=edX(r.b);g.fillStyle='#b0548a';g.fillRect(Math.max(ED_HEAD,a),ED_RULER-3,Math.max(1,b-Math.max(ED_HEAD,a)),3)});
  // tracks
  let y=ED_RULER;const T={};
  for(const tr of edTracks()){T[tr.id]={y,h:tr.h};g.fillStyle='#252529';g.fillRect(0,y,ED_HEAD,tr.h);g.fillStyle='#19191c';g.fillRect(ED_HEAD,y,w-ED_HEAD,tr.h);
    g.fillStyle='#d0d0d6';g.font='600 11px system-ui,Segoe UI,sans-serif';g.fillText(tr.name,8,y+tr.h/2-(tr.h>30?6:0));g.font='10px system-ui,Segoe UI,sans-serif';g.fillStyle='#8a8a94';if(tr.h>30)g.fillText(tr.sub,8,y+tr.h/2+8);else g.fillText(tr.sub,28,y+tr.h/2);
    g.fillStyle='#0f0f11';g.fillRect(0,y+tr.h-1,w,1);y+=tr.h}
  g.fillStyle='#0f0f11';g.fillRect(ED_HEAD-1,0,1,h);
  g.save();g.beginPath();g.rect(ED_HEAD,0,w-ED_HEAD,h);g.clip();
  g.font='11px system-ui,Segoe UI,sans-serif';
  const box=(x0,x1,yy,hh,fill,line,label,sel)=>{const a=Math.max(x0,ED_HEAD-2),b=Math.min(x1,w+2);if(b<=a)return;g.fillStyle=fill;g.fillRect(a,yy+1,b-a,hh-2);g.strokeStyle=sel?'#f2a33a':line;g.lineWidth=sel?2:1;g.strokeRect(a+0.5,yy+1.5,b-a-1,hh-3);g.lineWidth=1;
    if(label&&b-a>24){g.save();g.beginPath();g.rect(a+3,yy,b-a-6,hh);g.clip();g.fillStyle='#f4f4f6';g.fillText(label,a+5,yy+Math.min(hh/2,11));g.restore()}};
  // V1 video clips and A1 voice (linked)
  const pk=edPeaks(),V1=T.V1,A1=T.A1;
  M.clips.forEach((c,k)=>{const x0=edX(c.o),x1=edX(c.o+c.e-c.s);if(x1<ED_HEAD||x0>w)return;const sel=ED.sel&&ED.sel.type==='clip'&&Math.abs(ED.sel.s-c.s)<1e-3;
    const fr=edFrameFor(c);box(x0,x1,V1.y,V1.h,sel?'#4b6b9e':'#3d5a8a','#6283bd',`${STU.videoName||'Video'} · ${edTc(c.s)}${fr&&(fr.zoom!=='auto'||fr.hold)?' · framing set':''}`,sel);
    box(x0,x1,A1.y,A1.h,sel?'#2f6a4b':'#28583f','#3f8a63','',sel);
    if(pk){g.fillStyle='#7fd3a2';const mid=A1.y+A1.h/2,amp=A1.h/2-4;
      for(let x=Math.max(Math.ceil(x0),ED_HEAD);x<Math.min(x1,w);x++){const sa=c.s+edT(x)-c.o,sb=sa+1/ED.pps;let m=0;
        for(let i=Math.max(0,Math.floor(sa*100)),j=Math.min(pk.length-1,Math.ceil(sb*100));i<=j;i++)if(pk[i]>m)m=pk[i];
        const db=20*Math.log10(m+1e-6),hh=Math.max(0.5,Math.min(1,(db+54)/54))*amp;if(db>-54)g.fillRect(x,mid-hh,1,hh*2)}}
    if(ED.page==='fairlight'&&typeof ANA!=='undefined'&&ANA){g.fillStyle='#e0c060';for(const [bs,be] of ANA.breaths){if(be<c.s||bs>c.e)continue;const xa=edX(c.o+Math.max(bs,c.s)-c.s),xb=edX(c.o+Math.min(be,c.e)-c.s);g.fillRect(xa,A1.y+A1.h-6,Math.max(2,xb-xa),3)}}
    if(k>0&&M.clips[k-1].part!==c.part){g.fillStyle='#f0f0f0';g.fillRect(Math.round(x0),V1.y+2,1,V1.h+A1.h-4)}});
  // V2 covers
  M.covers.forEach(cv=>{const sel=ED.sel&&ED.sel.type==='cover'&&ED.sel.i===cv.i;const lab=cv.c.mode==='title'?'Title: '+(cv.c.title||''):cv.c.mode==='file'?'Picture: '+(cv.c.file?baseName(cv.c.file):'(choose)'):'Listening shots';
    box(edX(cv.a),edX(cv.b),T.V2.y,T.V2.h,sel?'#7b5aa8':'#644a8c','#8d6cc0',lab,sel)});
  // subtitles
  M.caps.forEach(cp=>{const x0=edX(cp.s),x1=edX(cp.e);if(x1<ED_HEAD||x0>w)return;box(x0,x1,T.ST.y,T.ST.h,'#5a4f2a','#8a7a3e',cp.t,false)});
  // in/out shading
  if(ED.inT!=null||ED.outT!=null){const a=edX(ED.inT??0),b=edX(ED.outT??M.total);g.fillStyle='rgba(255,255,255,.07)';g.fillRect(a,0,b-a,h);g.fillStyle='#e8e8ec';
    if(ED.inT!=null){g.fillRect(a,0,2,ED_RULER);g.fillText('I',a+4,ED_RULER-9)}if(ED.outT!=null){g.fillRect(b-2,0,2,ED_RULER);g.fillText('O',b-11,ED_RULER-9)}}
  // markers (chapters)
  M.marks.forEach(mk=>{const x=edX(mk.t);if(x<ED_HEAD-6||x>w+6)return;const sel=ED.sel&&ED.sel.type==='mark'&&ED.sel.i===mk.i;g.fillStyle=sel?'#ffd36b':'#3fa0e0';
    g.beginPath();g.moveTo(x-5,13);g.lineTo(x+5,13);g.lineTo(x+5,20);g.lineTo(x,25);g.lineTo(x-5,20);g.closePath();g.fill();
    if(ED.pps*60>40||sel){g.fillStyle='#cfe8ff';g.fillText(mk.c.title||'',x+8,18)}g.fillStyle='rgba(63,160,224,.35)';g.fillRect(x,ED_RULER,1,h-ED_RULER)});
  // trim ghost
  if(ED.drag&&ED.drag.x!=null){g.fillStyle='#f2a33a';g.fillRect(ED.drag.x-1,ED_RULER,2,h-ED_RULER)}
  if(ED.hover!=null&&ED.tool==='blade'){g.fillStyle='rgba(255,90,90,.8)';g.fillRect(ED.hover,V1.y,1,V1.h+A1.h)}
  // playhead
  const px=Math.round(edX(now))+0.5;g.fillStyle='#ff4040';g.strokeStyle='#ff4040';g.beginPath();g.moveTo(px-6,ED_RULER-12);g.lineTo(px+6,ED_RULER-12);g.lineTo(px+6,ED_RULER-6);g.lineTo(px,ED_RULER);g.lineTo(px-6,ED_RULER-6);g.closePath();g.fill();
  g.beginPath();g.moveTo(px,ED_RULER);g.lineTo(px,h);g.stroke();
  g.restore();
}

/* ---------- mouse on the timeline ---------- */
function edHit(x,y){
  const M=edModel(),t=edT(x);if(x<ED_HEAD)return {kind:'head'};
  if(y<ED_RULER){const mk=M.marks.find(m=>Math.abs(edX(m.t)-x)<6&&y>=10);return mk?{kind:'mark',i:mk.i,t}:{kind:'ruler',t}}
  let yy=ED_RULER;for(const tr of edTracks()){if(y>=yy&&y<yy+tr.h){
    if(tr.id==='V1'||tr.id==='A1'){const c=M.clips.find(c=>t>=c.o&&t<=c.o+c.e-c.s);if(!c)return {kind:'empty',t};
      const x0=edX(c.o),x1=edX(c.o+c.e-c.s),edge=x-x0<6&&x1-x0>14?'s':x1-x<6&&x1-x0>14?'e':null;return {kind:'clip',c,edge,t}}
    if(tr.id==='V2'){const cv=M.covers.find(cv=>t>=cv.a&&t<=cv.b);return cv?{kind:'cover',i:cv.i,t}:{kind:'empty',t}}
    if(tr.id==='ST'){const cp=M.caps.find(cp=>t>=cp.s&&t<=cp.e);return cp?{kind:'cap',cp,t}:{kind:'empty',t}}}yy+=tr.h}
  return {kind:'empty',t};
}
function edSnapT(t,skip){if(!ED.snap)return t;const lim=8/ED.pps;let best=t,d=lim;for(const p of [...edEditPoints(),edNow()]){if(skip!=null&&Math.abs(p-skip)<1e-6)continue;if(Math.abs(p-t)<d){d=Math.abs(p-t);best=p}}return best}
function edCanvasEvents(){
  const c=$('#edCanvas'),pos=e=>{const r=c.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top]};
  c.addEventListener('mousedown',e=>{
    const [x,y]=pos(e),h=edHit(x,y);c.focus();
    if(h.kind==='ruler'){ED.drag={kind:'scrub'};edSeek(edSnapT(h.t))}
    else if(h.kind==='mark'){ED.sel={type:'mark',i:h.i};edSeek(edModel().marks.find(m=>m.i===h.i).t);edInspect()}
    else if(h.kind==='clip'&&ED.tool==='blade'){edBladeAt(h.c.s+edSnapT(h.t)-h.c.o)}
    else if(h.kind==='clip'&&h.edge){ED.drag={kind:'trim',c:h.c,edge:h.edge,x}}
    else if(h.kind==='clip'){ED.sel={type:'clip',s:h.c.s,e:h.c.e};edInspect()}
    else if(h.kind==='cover'){ED.sel={type:'cover',i:h.i};edInspect()}
    else if(h.kind==='cap'){edSeek(h.cp.s)}
    else if(h.kind==='empty'){ED.sel=null;edSeek(edSnapT(h.t));edInspect();ED.drag={kind:'scrub'}}
    ED.drawKey='';e.preventDefault()});
  window.addEventListener('mousemove',e=>{
    const [x,y]=pos(e);
    if(ED.drag&&ED.drag.kind==='scrub'){edSeek(edSnapT(edT(x)))}
    else if(ED.drag&&ED.drag.kind==='trim'){const d=ED.drag,c0=d.c,base=d.edge==='e'?c0.o+c0.e-c0.s:c0.o;d.x=edX(edSnapT(edT(x),base));ED.drawKey='';
      const dt=(d.x-edX(base))/ED.pps;edSay(`${d.edge==='e'?'End':'Start'} ${dt>=0?'+':'−'}${Math.abs(dt).toFixed(2)} s`)}
    else if(e.target===c){const h=edHit(x,y);c.style.cursor=ED.tool==='blade'?'crosshair':h.kind==='clip'&&h.edge?'ew-resize':h.kind==='ruler'?'col-resize':'default';
      const hv=ED.tool==='blade'&&h.kind==='clip'?x:null;if(hv!==ED.hover){ED.hover=hv;ED.drawKey=''}}});
  window.addEventListener('mouseup',()=>{const d=ED.drag;ED.drag=null;if(!d)return;
    if(d.kind==='trim'&&d.x!=null){const c0=d.c,base=d.edge==='e'?c0.o+c0.e-c0.s:c0.o,dt=(d.x-edX(base))/ED.pps;if(Math.abs(dt)>0.01)edTrim(c0,d.edge,(d.edge==='e'?c0.e:c0.s)+dt)}
    ED.drawKey=''});
  c.addEventListener('wheel',e=>{e.preventDefault();const [x]=pos(e);
    if(e.ctrlKey||e.altKey||e.metaKey){edZoomTo(ED.pps*(e.deltaY<0?1.25:0.8),x)}
    else{ED.scroll=Math.max(0,ED.scroll+(Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY));edZoomUi();ED.drawKey=''}},{passive:false});
}

/* ---------- keyboard (only while the editor is open, never while typing) ---------- */
document.addEventListener('keydown',e=>{
  if(!ED.open)return;const tg=e.target,typing=tg&&(/^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName)||tg.isContentEditable);
  if(e.key==='Escape'){if(!$('#edHelp').hidden)$('#edHelp').hidden=true;else if(typing)tg.blur();else{ED.sel=null;ED.inT=ED.outT=null;edInspect()}ED.drawKey='';return}
  if(typing)return;const k=e.key,ctl=e.ctrlKey||e.metaKey,v=stuVideo(),f=1/edFps();let done=true;
  if(ctl&&(k==='z'||k==='Z')){e.shiftKey?edRedo():edUndo()}else if(ctl&&(k==='y'||k==='Y'))edRedo();
  else if(ctl&&(k==='b'||k==='B'||k==='\\'))edAct('split');
  else if(ctl&&(k==='='||k==='+'))edAct('zin');else if(ctl&&k==='-')edAct('zout');
  else if(ctl)done=false;
  else if(k===' ')edAct('play');else if(k==='k'||k==='K')edAct('stop');else if(k==='l'||k==='L')edAct('fwd');else if(k==='j'||k==='J')edAct('rev');
  else if(k==='ArrowLeft'){v.pause();ED.shuttle=0;edSeek(edNow()-(e.shiftKey?1:f))}else if(k==='ArrowRight'){v.pause();ED.shuttle=0;edSeek(edNow()+(e.shiftKey?1:f))}
  else if(k==='ArrowUp')edAct('prev');else if(k==='ArrowDown')edAct('next');else if(k==='Home')edAct('start');else if(k==='End')edAct('end');
  else if(e.altKey&&(e.code==='KeyX')){ED.inT=ED.outT=null;edSay('In and Out cleared.')}
  else if(k==='i'||k==='I')edAct('in');else if(k==='o'||k==='O')edAct('out');
  else if(k==='Delete'||k==='Backspace')edDelete();else if(k==='m'||k==='M')edMarker();
  else if(k==='a'||k==='A')edTool('select');else if(k==='b'||k==='B')edTool('blade');
  else if(k==='n'||k==='N'){ED.snap=!ED.snap;$('#edSnapBtn').classList.toggle('on',ED.snap);edSay(ED.snap?'Snapping on.':'Snapping off.')}
  else if(k==='Z'&&e.shiftKey)edFit();else if(k==='?')$('#edHelp').hidden=!$('#edHelp').hidden;
  else if(e.shiftKey&&/^Digit[1-5]$/.test(e.code))edShowPage(Object.keys(ED_PAGES)[+e.code.slice(5)-1]);
  else done=false;
  if(done){e.preventDefault();ED.drawKey=''}
},true);

/* ---------- Inspector (what is selected) ---------- */
function edInspect(){
  const box=$('#edInsp');if(!box||!ED.open)return;const s=ED.sel,M=edModel();
  if(s&&s.type==='clip'){const c=M.clips.find(c=>Math.abs(c.s-s.s)<1e-3);if(!c){ED.sel=null;return edInspect()}const fr=edFrameFor(c)||{zoom:'auto',hold:false},k=M.clips.indexOf(c);
    box.innerHTML=`<div class="ed-kv"><span>Clip</span><b>${k+1} of ${M.clips.length}</b><span>Source in</span><b>${edTc(c.s)}</b><span>Source out</span><b>${edTc(c.e)}</b><span>Duration</span><b>${edTc(c.e-c.s)}</b><span>Timeline in</span><b>${edTc(c.o)}</b></div>
      <label class="f"><span>Framing for this clip</span><select data-in="zoom"><option value="auto"${fr.zoom==='auto'?' selected':''}>Automatic</option><option value="1"${fr.zoom==='1'?' selected':''}>Wide (no close-up)</option><option value="1.18"${fr.zoom==='1.18'?' selected':''}>Close-up</option><option value="1.3"${fr.zoom==='1.3'?' selected':''}>Tight close-up</option></select></label>
      <label class="check"><input type="checkbox" data-in="hold"${fr.hold?' checked':''}> Hold the camera still</label>
      <div class="under" style="margin:4px 0 0"><button class="ed-b" data-ins="del">Ripple delete</button><button class="ed-b" data-ins="cover">Cover with other pictures</button><button class="ed-b" data-ins="reel">Make a Reel</button></div>`;return}
  if(s&&s.type==='cover'){const c=(STU.covers||[])[s.i];if(!c){ED.sel=null;return edInspect()}
    box.innerHTML=`<div class="ed-kv"><span>Cover</span><b>V2 · ${s.i+1}</b><span>From</span><b>${showTime(c.start)}</b><span>To</span><b>${showTime(c.end)}</b></div>
      <label class="f"><span>What it shows (the sound continues)</span><select data-in="mode"><option value="auto"${c.mode==='auto'?' selected':''}>Other moments where the speaker is silent</option><option value="file"${c.mode==='file'?' selected':''}>My photo or video…</option><option value="title"${c.mode==='title'?' selected':''}>Title card</option></select></label>
      ${c.mode==='title'?`<label class="f"><span>Title text</span><input data-in="title" data-live="1" value="${esc(c.title||'')}"></label>`:''}
      ${c.mode==='file'?`<div class="under" style="margin:0 0 8px"><button class="ed-b" data-ins="pick">Choose photo/video…</button><span class="status">${esc(c.file?baseName(c.file):'nothing chosen')}</span></div>`:''}
      <button class="ed-b" data-ins="del">Remove cover</button>`;return}
  if(s&&s.type==='mark'){const c=(STU.chapters||[])[s.i];if(!c){ED.sel=null;return edInspect()}const mk=M.marks.find(m=>m.i===s.i);
    box.innerHTML=`<div class="ed-kv"><span>Marker</span><b>Chapter</b><span>Timeline</span><b>${mk?edTc(mk.t):'—'}</b></div>
      <label class="f"><span>Chapter title (shown on YouTube)</span><input data-in="title" data-live="1" value="${esc(c.title||'')}"></label><button class="ed-b" data-ins="del">Remove marker</button>`;return}
  const words=Object.keys(STU.removed||{}).length;
  box.innerHTML=`<div class="ed-kv"><span>Timeline</span><b>${esc(STU.videoName||'—')}</b><span>Recording</span><b>${edTc(STU.dur||0)}</b><span>Edited</span><b>${edTc(M.total)}</b><span>Clips</span><b>${M.clips.length}</b>
    <span>Words cut</span><b>${words}</b><span>Hand cuts</span><b>${(STU.manualCuts||[]).length}</b><span>Covers</span><b>${M.covers.length}</b><span>Markers</span><b>${M.marks.length}</b></div>
    <p class="hint" style="margin:6px 0 0">Click a clip, cover or marker on the timeline to change it here.</p>
    ${(STU.manualCuts||[]).length?'<button class="ed-b" data-ins="clearcuts">Bring back all hand cuts</button>':''}`;
}
function edInspChange(e){
  const k=e.target.dataset.in,s=ED.sel;if(!k||!s)return;
  if(s.type==='clip'){const c=edModel().clips.find(c=>Math.abs(c.s-s.s)<1e-3);if(!c)return;let f=edFrameFor(c);
    if(!f){f={start:c.s,end:c.e,zoom:'auto',hold:false};STU.frames=[...(STU.frames||[]),f]}
    if(k==='zoom')f.zoom=e.target.value;if(k==='hold')f.hold=e.target.checked;
    if(f.zoom==='auto'&&!f.hold)STU.frames=STU.frames.filter(x=>x!==f);stuSave();try{picFramesRender()}catch(err){}ED.dirty=true;ED.drawKey='';return}
  if(s.type==='cover'){const c=STU.covers[s.i];c[k]=e.target.value;stuSave();try{picCoversRender()}catch(err){}ED.dirty=true;ED.drawKey='';if(k==='mode')edInspect();return}
  if(s.type==='mark'){STU.chapters[s.i].title=e.target.value;stuSave();try{stuRenderChapters()}catch(err){}ED.dirty=true;ED.drawKey=''}
}
async function edInspAct(a){
  const s=ED.sel;
  if(a==='del')return edDelete();
  if(a==='cover'||a==='reel')return edAct(a);
  if(a==='clearcuts'){STU.manualCuts=[];STU.splits=[];edChanged('All hand cuts brought back.');return}
  if(a==='pick'&&s&&s.type==='cover'){try{const d=await engChoose('cover');if(d.path){STU.covers[s.i].file=d.path;stuSave();try{picCoversRender()}catch(e){}edRefresh()}}catch(e){edSay(e.message)}}
}

/* ---------- Media pool, Fairlight info, colour preview and scopes ---------- */
function edPool(){
  const el=$('#edPoolList');if(!el)return;const v=(STU.info&&STU.info.streams||[]).find(s=>s.codec_type==='video');
  const item=(ic,name,meta)=>`<div class="ed-clipcard"><div class="ed-ic">${ic}</div><div><b>${esc(name)}</b><div class="status">${meta}</div></div></div>`;
  el.innerHTML=(STU.videoName?item('🎞',STU.videoName,[v?`${v.width}×${v.height}`:'',v?edFps()+' fps':'',STU.dur?edTc(STU.dur):''].filter(Boolean).join(' · ')):'')
    +(STU.settings.separate&&STU.audioName?item('🎙',STU.audioName,STU.sync?'synced ✓':'not synced yet'):'')
    +(STU.words.length?item('📝','Transcript',`${STU.words.length.toLocaleString()} words${STU.aligned?' · your exact text':''}`):'')
    ||'<p class="status">Nothing yet — choose your video below.</p>';
  $('#edProj').textContent=STU.videoName?'· '+STU.videoName:'';
}
function edMeters(){
  const el=$('#edMeterInfo');if(!el)return;const A=(typeof ANA!=='undefined')?ANA:null;const qc=$('#engQC');
  const sp=A?A.speech.reduce((a,[s,e])=>a+e-s,0):0;
  el.innerHTML=(A?`<div class="ed-kv"><span>Voice</span><b>${edTc(sp)}</b><span>Pauses</span><b>${Math.max(0,A.speech.length-1)}</b><span>Breaths</span><b>${A.breaths.length}</b><span>Room noise</span><b>${A.floor!=null?Math.round(A.floor)+' dB':'—'}</b></div>
    <p class="hint" style="margin:6px 0 0">Yellow marks under the waveform are breaths.</p>`:'<p class="hint">Press “Listen for pauses, breaths…” (Edit page) for the sound map; the waveform shows the camera sound until then.</p>')
    +(qc&&!qc.hidden&&qc.textContent?`<p class="hint" style="margin:8px 0 2px">Last render:</p><pre class="eng-qc">${esc(qc.textContent.split('\n').filter(l=>/Loud|Lip|peak/i.test(l)).join('\n'))}</pre>`:'');
}
function edGrade(){
  const v=stuVideo();if(!v)return;if(!ED.open){v.style.filter='';return}
  let c={};try{c=pic().colour}catch(e){}
  const br=+c.brightness||0,ct=+(c.contrast??1),sa=+(c.saturation??1),wm=+c.warmth||0;
  v.style.filter=`brightness(${(1+br).toFixed(3)}) contrast(${ct.toFixed(3)}) saturate(${sa.toFixed(3)})${wm>0?` sepia(${(wm*0.35).toFixed(3)})`:wm<0?` hue-rotate(${(wm*12).toFixed(1)}deg)`:''}`;
}
function edScope(){
  const v=stuVideo(),cv=$('#edScope');if(!cv||!v||v.readyState<2)return;const g=cv.getContext('2d'),W=cv.width,H=cv.height;
  const sw=128,sh=72;ED.off=ED.off||document.createElement('canvas');const o=ED.off;o.width=sw;o.height=sh;const og=o.getContext('2d',{willReadFrequently:true});
  og.filter=v.style.filter||'none';let d;try{og.drawImage(v,0,0,sw,sh);d=og.getImageData(0,0,sw,sh).data}catch(e){$('#edScopeNote').textContent='Scopes need the preview from the Studio Engine (or a video chosen on this computer).';return}
  g.fillStyle='#0b0b0d';g.fillRect(0,0,W,H);g.strokeStyle='#2c2c33';g.fillStyle='#6a6a74';g.font='9px system-ui';
  for(const p of [0,25,50,75,100]){const y=H-6-(H-12)*p/100;g.beginPath();g.moveTo(0,y+0.5);g.lineTo(W,y+0.5);g.stroke();g.fillText(String(p),2,y-2)}
  g.globalCompositeOperation='lighter';
  const plot=(cols,xoff,ch,colour)=>{g.fillStyle=colour;for(let x=0;x<sw;x++)for(let y=0;y<sh;y++){const i=(y*sw+x)*4;const val=ch<0?(0.2126*d[i]+0.7152*d[i+1]+0.0722*d[i+2]):d[i+ch];
    g.fillRect(xoff+x*cols/sw,H-6-(H-12)*val/255,Math.max(1,cols/sw),1)}};
  if(ED.scopeMode==='parade'){const cw=W/3;plot(cw,0,0,'rgba(255,70,70,.25)');plot(cw,cw,1,'rgba(70,255,90,.25)');plot(cw,2*cw,2,'rgba(80,140,255,.3)')}
  else plot(W,0,-1,'rgba(150,255,170,.22)');
  g.globalCompositeOperation='source-over';
}

/* ---------- start ---------- */
function edInit(){
  const _save=stuSave;stuSave=function(...a){edRecord();ED.dirty=true;ED.drawKey='';return _save.apply(this,a)};  // every change can be undone
  const _stats=stuStats;stuStats=function(...a){ED.dirty=true;ED.drawKey='';if(ED.open)setTimeout(()=>{edInspect();edPool();edMeters()},0);return _stats.apply(this,a)};
  ED.last=edSnap();
  const eo=$('#edOpen');if(eo)eo.addEventListener('click',edOpen);
  document.addEventListener('input',e=>{if(ED.open&&/^pic_/.test(e.target.id||''))setTimeout(edGrade,0)});
  document.addEventListener('change',e=>{if(ED.open&&/^pic_/.test(e.target.id||''))setTimeout(edGrade,0)});
}
