/* =====================================================================
   STUDIO · STAGE 3 — picture: framing, covers, captions, colour
   ===================================================================== */

/* ---------- 11. word-by-word ("karaoke") captions for Reels ---------- */
const CAP_STYLES={
  gold:{hi:'&H0048AEE0',label:'Gold highlight'},   // ASS colours are &HAABBGGRR
  white:{hi:'&H00FFFFFF',label:'White'},
  yellow:{hi:'&H0000E5FF',label:'Yellow highlight'},
};
/* One line at a time (max ~3 words / 22 characters for big readable text), current word highlighted.
   Placed above Instagram's bottom buttons and caption (safe zone), away from the right-hand icons. */
function makeAssWords(segs,opt={}){
  const W=1080,H=1920,st=CAP_STYLES[opt.style||'gold']||CAP_STYLES.gold,size=opt.size||84,upper=!!opt.upper;
  const m=mapper(segs),out=[];let cur=[];
  const words=[];STU.words.forEach((w,i)=>{if(STU.removed[i]||w.snd)return;const s=m.map(w.s),e=m.map(w.e);if(s==null||e==null)return;words.push({w:w.w,s,e})});
  const flush=()=>{if(cur.length)out.push(cur);cur=[]};
  words.forEach((w,i)=>{const prev=cur[cur.length-1];const len=cur.map(x=>x.w).join(' ').length+w.w.length;
    if(prev&&(w.s-prev.e>0.6||cur.length>=3||len>22))flush();cur.push(w);if(/[.?!]$/.test(w.w))flush()});flush();
  const fmt=t=>assT(Math.max(0,t));const clean=s=>(upper?s.toUpperCase():s).replace(/[{}\\]/g,'');
  const lines=[];
  out.forEach((ch,ci)=>{const end=ci+1<out.length?Math.min(out[ci+1][0].s,ch[ch.length-1].e+0.6):ch[ch.length-1].e+0.6;
    ch.forEach((w,k)=>{const s=k?w.s:Math.min(w.s,ch[0].s),e=k+1<ch.length?ch[k+1].s:end;if(e-s<0.02)return;
      const text=ch.map((x,j)=>j===k?`{\\c${st.hi}\\fscx108\\fscy108}${clean(x.w)}{\\c&H00FFFFFF\\fscx100\\fscy100}`:clean(x.w)).join(' ');
      lines.push(`Dialogue: 0,${fmt(s)},${fmt(e)},Words,,0,0,0,,${text}`)})});
  const marginV=Math.round(H*0.30);  // clear of the bottom 30% (caption, buttons, progress bar)
  return `[Script Info]\nScriptType: v4.00+\nPlayResX: ${W}\nPlayResY: ${H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Words,Arial,${size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H78000000,1,0,0,0,100,100,0,0,1,6,2,2,90,150,${marginV},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n${lines.join('\n')}\n`;
}

/* ---------- settings ---------- */
const PIC_DEFAULTS={layout:'crop169',punch:1.18,cutStyle:'punch',capStyle:'gold',capUpper:false,colour:{auto:true,brightness:0,contrast:1,saturation:1,warmth:0},bg:{mode:'none',colour:'#1e2a36',file:''},rect:null};
function pic(){const p=STU.settings.pic||{};return {...PIC_DEFAULTS,...p,colour:{...PIC_DEFAULTS.colour,...(p.colour||{})},bg:{...PIC_DEFAULTS.bg,...(p.bg||{})}}}
const parseTime=s=>{s=String(s||'').trim();if(!s)return NaN;const p=s.split(':').map(Number);if(p.some(isNaN))return NaN;return p.reduce((a,x)=>a*60+x,0)};
const showTime=t=>{t=Math.max(0,Math.round(t));return Math.floor(t/60)+':'+String(t%60).padStart(2,'0')};
function picOverrides(){return (STU.frames||[]).filter(f=>f.end>f.start).map(f=>({start:f.start,end:f.end,...(f.zoom!=='auto'&&f.zoom!=='box'?{zoom:+f.zoom}:{}),...(f.hold?{hold:true}:{}),...(f.rect?{rect:f.rect}:{}),...(f.kb?{kb:+f.kb}:{})}))}
function picRenderSettings(){const p=pic(),punch=p.cutStyle==='punch'?+p.punch:1;return {layout:p.layout,punch,cut_style:p.cutStyle,reelPunch:+p.punch>1?1.12:1,colour:p.colour,framing_overrides:picOverrides(),rect:p.rect||null,background:p.bg.mode!=='none'?{mode:p.bg.mode,colour:p.bg.colour,file:p.bg.file||null}:null}}
function picCovers(){return (STU.covers||[]).filter(c=>c.end>c.start).map(c=>({start:c.start,end:c.end,mode:c.mode,file:c.file||null,title:c.title||'',from:c.from??null}))}
function picReelAss(segs){const p=pic();return p.capStyle==='simple'?makeAss(segs):makeAssWords(segs,{style:p.capStyle,upper:p.capUpper})}

/* ---------- UI ---------- */
function picUi(){
  const p=pic();$('#pic_layout').value=p.layout;$('#pic_punch').value=String(p.punch);$('#pic_cutStyle').value=p.cutStyle;$('#picPunchRow').hidden=p.cutStyle!=='punch';$('#pic_auto').checked=p.colour.auto;
  ['brightness','contrast','saturation','warmth'].forEach(k=>{$('#pic_'+k).value=p.colour[k];$('#pic_'+k+'Val').textContent=(k==='brightness'||k==='warmth'?(p.colour[k]>0?'+':''):'')+(+p.colour[k]).toFixed(2)});
  $('#pic_capStyle').value=p.capStyle;$('#pic_capUpper').checked=p.capUpper;
  $('#pic_bgMode').value=p.bg.mode;$('#pic_bgColour').value=p.bg.colour;$('#picBgColourRow').hidden=p.bg.mode!=='colour';$('#picBgFileRow').hidden=p.bg.mode!=='image';
  $('#picBgFile').textContent=p.bg.file?baseName(p.bg.file):'nothing chosen';
  if(STU.matteNote)stuSay('#picMatteStatus',STU.matteNote);else stuSay('#picMatteStatus',p.bg.mode!=='none'?'Press “Find the person” once — it takes a while (it looks at every frame).':'');
  stuSay('#picRectStatus',p.rect?`Your box: ${Math.round(p.rect.w*100)}% of the width, from ${Math.round(p.rect.x*100)}% across and ${Math.round(p.rect.y*100)}% down.`:'Automatic framing.');
  $('#picOn').hidden=!ENG.on;$('#picOff').hidden=ENG.on;
  if(STU.facesPct!=null)stuSay('#picFacesStatus',`Speaker found: face visible in ${STU.facesPct}% of the video ✓`);
  picCoversRender();
}
function picSave(){
  const old=pic();
  const c={...old.colour,auto:$('#pic_auto').checked};['brightness','contrast','saturation','warmth'].forEach(k=>c[k]=+$('#pic_'+k).value);  // (keeps wheels, look, LUT)
  STU.settings.pic={...old,layout:$('#pic_layout').value,punch:+$('#pic_punch').value,cutStyle:$('#pic_cutStyle').value,capStyle:$('#pic_capStyle').value,capUpper:$('#pic_capUpper').checked,colour:c,
    bg:{mode:$('#pic_bgMode').value,colour:$('#pic_bgColour').value,file:old.bg.file},rect:old.rect};
  stuSave();picUi();
}
function picCoversRender(){
  const box=$('#picCovers');const cs=STU.covers||[];
  box.innerHTML=cs.map((c,i)=>`<div class="cover-row">
    <input data-cv="${i}" data-k="start" value="${showTime(c.start)}" aria-label="From (m:ss)" title="From (m:ss)">
    <input data-cv="${i}" data-k="end" value="${showTime(c.end)}" aria-label="To (m:ss)" title="To (m:ss)">
    <select data-cv="${i}" data-k="mode"><option value="auto"${c.mode==='auto'?' selected':''}>Other moments where the speaker is silent</option><option value="file"${c.mode==='file'?' selected':''}>My photo or video…</option><option value="title"${c.mode==='title'?' selected':''}>Title card</option></select>
    <button class="btn small" data-cvdel="${i}" title="Remove">✕</button>
    <div class="cover-extra">${c.mode==='file'?`<button class="btn small" data-cvpick="${i}">Choose photo/video…</button><span class="status">${esc(c.file?baseName(c.file):'nothing chosen')}</span>`:''}${c.mode==='title'?`<input data-cv="${i}" data-k="title" value="${esc(c.title||'')}" placeholder="Title text, e.g. Divine Light Meditation" style="flex:1">`:''}${c.mode==='auto'?'<span class="status">If there are no silent moments long enough, a title card is used.</span>':''}</div></div>`).join('')||'<p class="status">Nothing covered.</p>';
}
function picFramesRender(){
  const fs=STU.frames||[];
  $('#picFrames').innerHTML=fs.map((f,i)=>`<div class="cover-row">
    <input data-fr="${i}" data-k="start" value="${showTime(f.start)}" aria-label="From (m:ss)" title="From (m:ss)">
    <input data-fr="${i}" data-k="end" value="${showTime(f.end)}" aria-label="To (m:ss)" title="To (m:ss)">
    <select data-fr="${i}" data-k="zoom"><option value="auto"${f.zoom==='auto'?' selected':''}>Automatic</option><option value="1"${f.zoom==='1'?' selected':''}>Wide (no close-up)</option><option value="1.18"${f.zoom==='1.18'?' selected':''}>Close-up</option><option value="1.3"${f.zoom==='1.3'?' selected':''}>Tight close-up</option>${f.rect?`<option value="box" selected>My own box</option>`:''}</select>
    <button class="btn small" data-frdel="${i}" title="Remove">✕</button>
    <div class="cover-extra"><label class="check" style="margin:0"><input type="checkbox" data-fr="${i}" data-k="hold"${f.hold?' checked':''}> Hold the camera still in this section</label></div></div>`).join('')||'<p class="status">Nothing set by hand.</p>';
}
function picInit(){
  picFramesRender();
  $('#picFrameAdd').addEventListener('click',()=>{const v=stuVideo(),t=v&&v.currentTime>0?v.currentTime:0;STU.frames=[...(STU.frames||[]),{start:t,end:t+30,zoom:'auto',hold:false}];stuSave();picFramesRender()});
  $('#picFrames').addEventListener('change',e=>{const i=e.target.dataset.fr;if(i==null)return;const f=STU.frames[+i],k=e.target.dataset.k;
    if(k==='start'||k==='end'){const t=parseTime(e.target.value);if(isNaN(t)){alert('Write the time as minutes:seconds, e.g. 1:00');setTimeout(picFramesRender,0);return}f[k]=t}
    else if(k==='hold')f.hold=e.target.checked;else f[k]=e.target.value;
    if(f.end<=f.start)stuSay('#picStatus',`Section ${+i+1}: the end must be after the start.`);stuSave();setTimeout(picFramesRender,0)});
  $('#picFrames').addEventListener('click',e=>{const b=e.target.closest('button');if(b&&b.dataset.frdel!=null){STU.frames.splice(+b.dataset.frdel,1);stuSave();picFramesRender()}});
  STU.settings.pic=pic();picUi();
  ['pic_layout','pic_punch','pic_cutStyle','pic_auto','pic_capStyle','pic_capUpper','pic_bgMode','pic_bgColour'].forEach(id=>$('#'+id).addEventListener('change',picSave));
  $('#picBgPick').addEventListener('click',async()=>{try{const d=await engChoose('cover');if(d.path){const p=pic();STU.settings.pic={...p,bg:{...p.bg,file:d.path}};stuSave();picUi()}}catch(e){alert(e.message)}});
  $('#picMatte').addEventListener('click',async()=>{
    if(!engNeed())return;if(!STU.engPrep){alert('Press “Prepare” in step 1 first.');return}
    stuSay('#picMatteStatus','Finding the person in every frame (see progress at the top)…');
    try{const r=await engRun('matte',{video:STU.engVideo});STU.matteNote=r.message;stuSave();stuSay('#picMatteStatus',r.message);if(r.person_pct<3)alert('Hardly any person was found in this video, so changing the background will not look right. Use “Keep the real background”, or check that the speaker is clearly visible.')}catch(e){stuSay('#picMatteStatus',e.message)}});
  $('#picRectDraw').addEventListener('click',()=>{if(typeof edOpen==='function'){edOpen();edShowPage('color');edFrameEdit('video')}});
  $('#picRectAuto').addEventListener('click',()=>{STU.settings.pic={...pic(),rect:null};stuSave();picUi();if(typeof edFramePreview==='function')edFramePreview()});
  ['brightness','contrast','saturation','warmth'].forEach(k=>$('#pic_'+k).addEventListener('input',picSave));
  $('#picReset').addEventListener('click',()=>{STU.settings.pic={...pic(),colour:{...PIC_DEFAULTS.colour}};stuSave();picUi()});
  $('#picFaces').addEventListener('click',async()=>{
    if(!engNeed())return;if(!STU.engPrep){alert('Press “Prepare” in step 1 first.');return}
    stuSay('#picFacesStatus','The engine is looking at the picture (see progress above)…');
    try{const r=await engRun('faces',{video:STU.engVideo});STU.facesPct=r.seen_pct;stuSave();
      stuSay('#picFacesStatus',r.seen_pct>=60?`Speaker found: face visible in ${r.seen_pct}% of the video ✓`:`Face visible in only ${r.seen_pct}% of the video — framing will stay mostly centred.`)}
    catch(e){stuSay('#picFacesStatus',e.message)}});
  const still=async closeup=>{
    if(!engNeed())return;const v=stuVideo(),t=v&&v.currentTime>0?v.currentTime:Math.max(0,(STU.dur||60)*0.25);
    stuSay('#picStatus',`Making a still at ${fmtT(t)}…`);
    try{const r=await engRun('frame',{video:STU.engVideo,t,closeup,settings:{res:'1080',...picRenderSettings()}});
      const img=$('#picStill');img.src=engFileUrl(r.frame);img.hidden=false;stuSay('#picStatus',`Still at ${fmtT(t)}${closeup?' (close-up)':''}, ${r.size[0]}×${r.size[1]}. Move the video player and press again to check other moments.`)}
    catch(e){stuSay('#picStatus',e.message)}};
  $('#picPreview').addEventListener('click',()=>still(false));$('#picPreviewClose').addEventListener('click',()=>still(true));
  $('#picCoverAdd').addEventListener('click',()=>{const v=stuVideo(),t=v&&v.currentTime>0?v.currentTime:0;STU.covers=[...(STU.covers||[]),{start:t,end:t+(t?10:60),mode:'auto'}];stuSave();picCoversRender()});
  $('#picCovers').addEventListener('change',e=>{const i=e.target.dataset.cv;if(i==null)return;const c=STU.covers[+i],k=e.target.dataset.k;
    if(k==='start'||k==='end'){const t=parseTime(e.target.value);if(isNaN(t)){alert('Write the time as minutes:seconds, e.g. 1:00');setTimeout(picCoversRender,0);return}c[k]=t}else c[k]=e.target.value;
    if(c.end<=c.start)stuSay('#picStatus',`Cover ${+i+1}: the end (${showTime(c.end)}) must be after the start (${showTime(c.start)}).`);
    stuSave();setTimeout(picCoversRender,0)});  // redraw after the field has finished losing focus
  $('#picCovers').addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.cvdel!=null){STU.covers.splice(+b.dataset.cvdel,1);stuSave();picCoversRender()}
    if(b.dataset.cvpick!=null){try{const d=await engChoose('cover');if(d.path){STU.covers[+b.dataset.cvpick].file=d.path;stuSave();picCoversRender()}}catch(err){alert(err.message)}}});
}
