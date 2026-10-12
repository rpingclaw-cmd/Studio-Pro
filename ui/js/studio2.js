/* =====================================================================
   STUDIO · STAGE 2 — your accurate transcript, smarter cuts, chapters
   ===================================================================== */

/* ---------- 6. line up the owner's accurate transcript with the recording ---------- */
const normTok=w=>String(w||'').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g,'').replace(/[’‘`]/g,"'").replace(/[^\p{L}\p{N}']/gu,'').replace(/^'+|'+$/g,'');
function levRatio(a,b){
  if(a===b)return 1;if(!a||!b)return 0;const m=a.length,n=b.length;let prev=new Array(n+1),cur=new Array(n+1);
  for(let j=0;j<=n;j++)prev[j]=j;
  for(let i=1;i<=m;i++){cur[0]=i;for(let j=1;j<=n;j++)cur[j]=Math.min(prev[j]+1,cur[j-1]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1));[prev,cur]=[cur,prev]}
  return 1-prev[n]/Math.max(m,n);
}
/* Longest increasing run of anchor pairs (keeps them in order on both sides) */
function lisPairs(pairs){
  const tails=[],prevIdx=new Array(pairs.length),tailIdx=[];
  pairs.forEach((p,k)=>{let lo=0,hi=tails.length;while(lo<hi){const mid=(lo+hi)>>1;if(tails[mid]<p[1])lo=mid+1;else hi=mid}
    tails[lo]=p[1];tailIdx[lo]=k;prevIdx[k]=lo?tailIdx[lo-1]:-1});
  const out=[];let k=tailIdx[tails.length-1];while(k!=null&&k>=0){out.push(pairs[k]);k=prevIdx[k]}return out.reverse();
}
/* Word-by-word alignment of a short stretch (dynamic programming). Returns [[i|-1, j|-1, kind]] */
function dpAlign(O,H,i0,i1,j0,j1){
  const n=i1-i0,m=j1-j0,out=[];
  if(!n&&!m)return out;
  if(n*m>3e6){ // very long unmatched stretch: spread evenly, everything 'unsure'
    for(let i=0;i<n;i++)out.push([i0+i,m?j0+Math.min(m-1,Math.floor(i*m/n)):-1,'sub']);
    return out}
  const W=m+1,S=new Float32Array((n+1)*W),B=new Uint8Array((n+1)*W);const GAP=-1;
  const sc=(i,j)=>{const a=O[i0+i].n,b=H[j0+j].n;if(a===b)return 2;const r=levRatio(a,b);return r>=0.75?1:-1};
  for(let i=1;i<=n;i++){S[i*W]=i*GAP;B[i*W]=1}for(let j=1;j<=m;j++){S[j]=j*GAP;B[j]=2}
  for(let i=1;i<=n;i++)for(let j=1;j<=m;j++){const d=S[(i-1)*W+j-1]+sc(i-1,j-1),u=S[(i-1)*W+j]+GAP,l=S[i*W+j-1]+GAP;
    if(d>=u&&d>=l){S[i*W+j]=d;B[i*W+j]=0}else if(u>=l){S[i*W+j]=u;B[i*W+j]=1}else{S[i*W+j]=l;B[i*W+j]=2}}
  let i=n,j=m;while(i>0||j>0){const b=B[i*W+j];
    if(i>0&&j>0&&b===0){const s=sc(i-1,j-1);out.push([i0+i-1,j0+j-1,s===2?'same':s===1?'near':'sub']);i--;j--}
    else if(i>0&&(j===0||b===1)){out.push([i0+i-1,-1,'ins']);i--}else{out.push([-1,j0+j-1,'del']);j--}}
  return out.reverse();
}
/* owner text + recognised words (with times) -> owner's exact words with times */
function alignTranscript(text,asr){
  const O=String(text).split(/\s+/).filter(Boolean).map(w=>({w,n:normTok(w)})).filter(o=>o.n||o.w.length);
  const H=asr.map(w=>({...w,n:normTok(w.w)}));
  if(!O.length)throw new Error('The transcript is empty.');
  if(!H.length)throw new Error('Transcribe the recording first (the button above) — its word timings are needed to line up your text.');
  // anchors: three-word phrases that appear exactly once on both sides
  const grams=(arr)=>{const m=new Map();for(let i=0;i+2<arr.length;i++){const k=arr[i].n+' '+arr[i+1].n+' '+arr[i+2].n;m.set(k,m.has(k)?-1:i)}return m};
  const go=grams(O),gh=grams(H),pairs=[];
  for(const [k,i] of go){if(i<0)continue;const j=gh.get(k);if(j!=null&&j>=0)pairs.push([i,j])}
  pairs.sort((a,b)=>a[0]-b[0]);
  const anchors=lisPairs(pairs);const link=[];let pi=0,pj=0;
  for(const [i,j] of anchors){if(i<pi||j<pj)continue;link.push(...dpAlign(O,H,pi,i,pj,j));for(let k=0;k<3;k++)link.push([i+k,j+k,'same']);pi=i+3;pj=j+3}
  link.push(...dpAlign(O,H,pi,O.length,pj,H.length));
  // build the word list in spoken order
  const words=[];let matched=0;
  for(const [i,j,kind] of link){
    if(i>=0&&j>=0){const h=H[j];words.push({w:O[i].w,s:h.s,e:h.e,...(kind==='sub'?{u:1}:{})});if(kind!=='sub')matched++}
    else if(i>=0)words.push({w:O[i].w,s:null,e:null,u:1});
    else{const h=H[j];words.push({w:h.w,s:h.s,e:h.e,x:1})}
  }
  // owner words the recogniser did not hear: share the time between their neighbours
  for(let k=0;k<words.length;k++){if(words[k].s!=null)continue;let a=k;while(a<words.length&&words[a].s==null)a++;
    const prev=words[k-1],next=words[a];const t0=prev?prev.e:Math.max(0,(next?next.s:0)-0.3*(a-k)),t1=next?next.s:t0+0.3*(a-k);
    const run=words.slice(k,a),tot=run.reduce((s,w)=>s+w.w.length+1,0)||1;let t=t0;
    run.forEach(w=>{const d=(t1-t0)*(w.w.length+1)/tot;w.s=t;w.e=t+Math.max(0,d);t+=d});k=a-1}
  return {words,matched,total:O.length,extra:words.filter(w=>w.x).length,unsure:words.filter(w=>w.u).length};
}
async function readTranscriptFile(f){
  if(/\.docx$/i.test(f.name)){
    if(!window.JSZip)throw new Error('The zip tool did not load. Refresh and try again.');
    const z=await JSZip.loadAsync(await f.arrayBuffer());const xml=await z.file('word/document.xml').async('string');
    return xml.replace(/<w:tab\/>/g,' ').replace(/<\/w:p>/g,'\n').replace(/<w:br\/>/g,'\n').replace(/<[^>]+>/g,'')
      .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&apos;/g,"'");
  }
  if(/\.(srt|vtt)$/i.test(f.name))return (await f.text()).split(/\r?\n/).filter(l=>l.trim()&&!/^\d+$/.test(l.trim())&&!/-->/.test(l)&&!/^WEBVTT/.test(l)).join(' ');
  return await f.text();
}

/* ---------- 7. the sound map from the engine: quiet points, real pauses, breaths, missed "um"s ---------- */
let ANA=null; // {env:Uint8Array (dB+100, 10 ms), speech:[[s,e]], breaths:[[s,e]], floor}
function anaSet(d){
  if(!d){ANA=null;return}
  const bin=atob(d.env_b64),env=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)env[i]=bin.charCodeAt(i);
  ANA={env,speech:d.speech||[],breaths:d.breaths||[],floor:d.noise_floor_db};
}
/* the quietest moment between t0 and t1 (falls back to the middle) */
function quietest(t0,t1){
  if(!ANA||!(t1>t0))return (t0+t1)/2;
  let a=Math.max(0,Math.floor(t0*100)),b=Math.min(ANA.env.length-1,Math.ceil(t1*100));if(b<=a)return (t0+t1)/2;
  let best=a,v=999;for(let i=a;i<=b;i++){const x=ANA.env[i]+(i>0?ANA.env[i-1]:ANA.env[i])+(i+1<ANA.env.length?ANA.env[i+1]:ANA.env[i]);if(x<v){v=x;best=i}}
  return Math.min(t1,Math.max(t0,best/100+0.005));
}
/* pauses by what you hear: silence between voice stretches, longer than the setting -> shortened.
   Breaths just before speech are kept when "keep natural breaths" is on. */
function anaPauseCuts(mp,keepBreaths){
  if(!ANA)return [];const cuts=[],sp=ANA.speech;
  for(let k=1;k<sp.length;k++){const a=sp[k-1][1],b=sp[k][0];if(b-a<=mp)continue;
    let s=a+mp/2,e=b-mp/2;
    if(keepBreaths){const br=ANA.breaths.find(x=>x[0]>=a-0.05&&x[1]<=b+0.05);if(br){e=Math.min(e,br[0]-0.06);if(e-s<0.15)continue}}
    s=quietest(s-0.05,s+0.05);e=quietest(e-0.05,e+0.05);if(e-s>0.12)cuts.push([s,e])}
  return cuts;
}
/* voice the transcript has no words for (um, uh, false starts): added to the transcript as [sound] items */
function anaMissedSounds(words){
  if(!ANA)return [];const out=[];let k=0;
  for(const [s,e] of ANA.speech){
    while(k<words.length&&words[k].e<s)k++;
    let has=false;for(let i=k;i<words.length&&words[i].s<e;i++){if(words[i].e>s&&!words[i].snd){has=true;break}}
    const d=e-s;if(!has&&d>=0.15&&d<=1.5)out.push({w:`[sound ${d.toFixed(1)} s]`,s,e,x:1,snd:1});
  }
  return out;
}
function anaMergeSounds(){
  const base=STU.words.filter(w=>!w.snd);const extra=anaMissedSounds(base);
  const removedByWord=new Map(STU.words.map((w,i)=>[w,STU.removed[i]]));
  STU.words=[...base,...extra].sort((a,b)=>a.s-b.s);
  const r={};STU.words.forEach((w,i)=>{const v=removedByWord.get(w);if(v)r[i]=v});STU.removed=r;
  return extra.length;
}

/* ---------- 8. chapters (YouTube timestamps) ---------- */
async function stuSuggestChapters(){
  if(!STU.words.length){stuSay('#stuChapStatus','Transcribe first.');return}
  const keep=keepSegments(),m=mapper(keep);const lines=[];let cur=[];
  STU.words.forEach((w,i)=>{if(STU.removed[i]||w.snd)return;cur.push(i);if(/[.?!]$/.test(w.w)||cur.length>60){lines.push(cur);cur=[]}});if(cur.length)lines.push(cur);
  const text=lines.map((l,k)=>`[${k}] (${fmtT(m.map(STU.words[l[0]].s)??0)}) ${l.map(i=>STU.words[i].w).join(' ')}`).join('\n');
  stuSay('#stuChapStatus','Asking AI for chapters…');
  try{
    const got=await askAny(`Below is the transcript of a recorded spiritual talk (Pranahuti Yoga / Sahaj Marg tradition), split into numbered lines with their time.
Make YouTube chapters: 4 to 12 chapters for the whole talk, each at least 1 minute long, the first one starting at line 0. Titles: short (2 to 6 words), clear, respectful, in the talk's own language; do not invent content.
Reply with only JSON: {"chapters":[{"line":0,"title":"Introduction"}]}

TRANSCRIPT:
${text}`,{json:true,onStatus:t=>stuSay('#stuChapStatus',t)});
    let arr=parseJSON(got.text);arr=Array.isArray(arr)?arr:(arr.chapters||[]);
    STU.chapters=arr.filter(c=>c.line>=0&&c.line<lines.length).map(c=>({t:STU.words[lines[c.line][0]].s,title:String(c.title||'').slice(0,80)})).sort((a,b)=>a.t-b.t);
    if(STU.chapters.length)STU.chapters[0].t=Math.min(STU.chapters[0].t,keep.length?keep[0][0]:0);
    stuSave();stuRenderChapters();stuSay('#stuChapStatus',`${STU.chapters.length} chapters suggested (with ${PROVIDERS[got.pid].label}). Edit the titles, remove or add as you like.`);
  }catch(e){stuSay('#stuChapStatus',errMsg(e))}
}
/* chapter times on the EDITED video (what YouTube needs) */
function chapterEditedTime(c,keep,m){let t=m.map(c.t);if(t==null){const seg=keep.find(([s])=>s>=c.t);t=seg?m.map(seg[0]):null}return t}
function chaptersEdited(){
  const keep=keepSegments(),m=mapper(keep);const out=[];
  for(const c of STU.chapters||[]){const t=chapterEditedTime(c,keep,m);if(t!=null)out.push({t,title:c.title})}
  out.sort((a,b)=>a.t-b.t);if(out.length)out[0].t=0;return out;
}
const ytTime=t=>{t=Math.max(0,Math.floor(t));const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(s).padStart(2,'0')};
function chaptersText(){return chaptersEdited().map(c=>`${ytTime(c.t)} ${c.title}`).join('\n')}
function stuRenderChapters(){
  const box=$('#stuChapters');if(!box)return;const ed=chaptersEdited(),keep=keepSegments(),m=mapper(keep);
  box.innerHTML=(STU.chapters||[]).map((c,i)=>{const t=i===0&&ed.length?0:chapterEditedTime(c,keep,m);return `<div class="chap"><span class="status">${t!=null?ytTime(t):'—'}</span><input data-chap="${i}" value="${esc(c.title)}"><button class="btn small" data-chapgo="${i}" title="Play">▶</button><button class="btn small" data-chapdel="${i}" title="Remove">✕</button></div>`}).join('')||'<p class="status">No chapters yet.</p>';
  const warn=[];if(ed.length&&ed.length<3)warn.push('YouTube needs at least 3 chapters.');
  for(let i=1;i<ed.length;i++)if(ed[i].t-ed[i-1].t<10){warn.push('Each chapter must be at least 10 seconds long.');break}
  stuSay('#stuChapWarn',warn.join(' '));
}

/* ---------- wiring ---------- */
function stage2Init(){
  $('#stuOwnFile').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;try{$('#stuOwnText').value=await readTranscriptFile(f);STU.ownText=$('#stuOwnText').value;stuSave();stuSay('#stuAlignStatus',`Loaded ${f.name}: ${STU.ownText.split(/\s+/).filter(Boolean).length.toLocaleString()} words.`)}catch(err){stuSay('#stuAlignStatus','Could not read that file: '+err.message)}e.target.value=''});
  $('#stuOwnText').addEventListener('change',e=>{STU.ownText=e.target.value;stuSave()});
  if(STU.ownText)$('#stuOwnText').value=STU.ownText;
  $('#stuAlign').addEventListener('click',()=>{
    const text=$('#stuOwnText').value.trim();STU.ownText=text;
    const asr=(STU.asrWords&&STU.asrWords.length?STU.asrWords:STU.words.filter(w=>!w.snd&&!w.u&&!w.x));
    if(STU.words.some((w,i)=>STU.removed[i]==='user')&&!confirm('Lining up replaces the transcript in the editor; your manual cuts will be cleared. Continue?'))return;
    try{if(!STU.asrWords||!STU.asrWords.length)STU.asrWords=asr.map(({w,s,e})=>({w,s,e}));
      const r=alignTranscript(text,STU.asrWords);STU.words=r.words;STU.removed={};STU.aligned=true;
      if(ANA)anaMergeSounds();autoMarks();stuSave();stuRenderTranscript();stuRenderChapters();
      const pct=(100*r.matched/r.total);STU.alignStats={pct,unsure:r.unsure,extra:r.extra};
      stuSay('#stuAlignStatus',`Lined up: ${pct.toFixed(1)}% of your words matched the recording${pct>=98?' ✓':''}. ${r.unsure} word(s) to check (orange), ${r.extra} spoken word(s) not in your text (grey: um, repeats — cut automatically if ticked below).`);
    }catch(e){stuSay('#stuAlignStatus',e.message)}});
  $('#stuNextUnsure').addEventListener('click',()=>{const v=stuVideo(),t=v?v.currentTime:0;
    let i=STU.words.findIndex(w=>w.u&&w.s>t+0.3);if(i<0)i=STU.words.findIndex(w=>w.u);if(i<0){alert('Nothing left to check.');return}
    const el=$(`#stuText [data-i="${i}"]`);if(el){el.scrollIntoView({block:'center'});el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),1500)}if(v&&v.src){v.currentTime=Math.max(0,STU.words[i].s-1.5);v.play().catch(()=>{})}});
  $('#stu_extraCut').addEventListener('change',e=>{STU.settings.extraCut=e.target.checked;autoMarks();stuSave();stuRenderTranscript()});
  $('#stu_keepBreaths').addEventListener('change',e=>{STU.settings.keepBreaths=e.target.checked;stuSave();stuStats()});
  $('#stu_xfade').addEventListener('change',e=>{STU.settings.xfade=Math.max(0,Math.min(200,+e.target.value||0));stuSave()});
  $('#stu_extraCut').checked=STU.settings.extraCut!==false;$('#stu_keepBreaths').checked=STU.settings.keepBreaths!==false;$('#stu_xfade').value=STU.settings.xfade??40;
  $('#stuAnalyse').addEventListener('click',async()=>{
    if(!ENG.on){alert('This needs the Studio Engine (top of this tab).');return}if(!engNeed(true))return;
    stuSay('#stuAnaStatus','The engine is listening to the cleaned sound (see progress above)…');
    try{const r=await engRun('analyse',engParams());const d=await (await fetch(engFileUrl(r.analysis))).json();anaSet(d);STU.anaFile=r.analysis;
      const n=STU.words.length?anaMergeSounds():0;autoMarks();await stuSave();stuRenderTranscript();
      stuSay('#stuAnaStatus',`${r.message} ${n?`${n} sound(s) without words added to the transcript as [sound] (grey).`:''} Cuts now land on the quietest moment; pauses are measured by what you hear.`)}
    catch(e){stuSay('#stuAnaStatus',e.message)}});
  $('#stuChapSuggest').addEventListener('click',stuSuggestChapters);
  $('#stuChapAdd').addEventListener('click',()=>{const v=stuVideo();STU.chapters=[...(STU.chapters||[]),{t:v?v.currentTime:0,title:'New chapter'}].sort((a,b)=>a.t-b.t);stuSave();stuRenderChapters()});
  $('#stuChapters').addEventListener('input',e=>{const i=e.target.dataset.chap;if(i!=null){STU.chapters[+i].title=e.target.value;stuSave()}});
  $('#stuChapters').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.chapdel!=null){STU.chapters.splice(+b.dataset.chapdel,1);stuSave();stuRenderChapters()}
    if(b.dataset.chapgo!=null){const v=stuVideo();if(v.src){v.currentTime=STU.chapters[+b.dataset.chapgo].t;v.play().catch(()=>{})}}});
  $('#stuChapCopy').addEventListener('click',()=>{const t=chaptersText();navigator.clipboard.writeText(t).then(()=>stuSay('#stuChapStatus','Copied — paste into the YouTube description.')).catch(()=>prompt('Copy:',t))});
  stuRenderChapters();
  if(STU.anaFile&&ENG.on)fetch(engFileUrl(STU.anaFile)).then(r=>r.json()).then(d=>{anaSet(d);stuStats()}).catch(()=>{});
}
