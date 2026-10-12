/* =====================================================================
   STUDIO — podcast / talk editor. Heavy video work is done by ffmpeg on
   the user's own computer; the browser does sync, transcript, editing.
   ===================================================================== */
const STU_DEFAULTS={clean:'standard',res:'1080',enc:'auto',lang:'',asr:'accurate',keepAll:true,fill:'auto',fillTitle:'',fillFile:'',fillers:true,pauses:true,maxPause:1.0,trimEnds:true,srt:true,reelCaps:true,reelRes:'1080',separate:true,proxy:false};
let STU={videoName:'',audioName:'',videoFile:null,proxyFile:null,info:null,wavV:null,wavA:null,dur:0,sync:null,words:[],removed:{},reels:[],settings:{...STU_DEFAULTS},transcribed:false};
const FILLERS=new Set(['um','umm','uh','uhh','erm','er','ah','hmm','mm','mmm','uhm']);
function stuSay(id,t){const el=$(id);if(el)el.textContent=t}
async function stuSave(){try{const {videoFile,proxyFile,wavV,wavA,...rest}=STU;await IDB.set('studio',rest)}catch(e){}}
async function stuLoad(){try{await IDB.open();const s=await IDB.get('studio');if(s){STU={...STU,...s,settings:{...STU_DEFAULTS,...(s.settings||{})}}}}catch(e){}}
const fmtT=t=>{t=Math.max(0,t);const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=(t%60);return (h?h+':'+String(m).padStart(2,'0'):m)+':'+s.toFixed(1).padStart(4,'0')};
const fx=n=>(+n).toFixed(3);

/* ---------- WAV + DSP ---------- */
function parseWav(buf){
  const dv=new DataView(buf);if(dv.getUint32(0,false)!==0x52494646)throw new Error('Not a WAV file');
  let p=12,fmt=null,data=null;
  while(p+8<=dv.byteLength){const id=String.fromCharCode(dv.getUint8(p),dv.getUint8(p+1),dv.getUint8(p+2),dv.getUint8(p+3));const sz=dv.getUint32(p+4,true);
    if(id==='fmt ')fmt={ch:dv.getUint16(p+10,true),rate:dv.getUint32(p+12,true),bits:dv.getUint16(p+22,true)};
    if(id==='data'){data={off:p+8,len:Math.min(sz,dv.byteLength-p-8)};break}
    p+=8+sz+(sz&1)}
  if(!fmt||!data||fmt.bits!==16)throw new Error('Unexpected WAV format — use the prepare file to make it');
  const n=Math.floor(data.len/2/fmt.ch);const out=new Float32Array(n);const s16=new Int16Array(buf,data.off,n*fmt.ch);
  for(let i=0;i<n;i++)out[i]=s16[i*fmt.ch]/32768;
  return {rate:fmt.rate,x:out};
}
function fftInPlace(re,im,inv){
  const n=re.length;for(let i=1,j=0;i<n;i++){let bit=n>>1;for(;j&bit;bit>>=1)j^=bit;j^=bit;if(i<j){[re[i],re[j]]=[re[j],re[i]];[im[i],im[j]]=[im[j],im[i]]}}
  for(let len=2;len<=n;len<<=1){const ang=2*Math.PI/len*(inv?1:-1),wr=Math.cos(ang),wi=Math.sin(ang);
    for(let i=0;i<n;i+=len){let cr=1,ci=0;for(let k=0;k<len/2;k++){const a=i+k,b=a+len/2;const tr=re[b]*cr-im[b]*ci,ti=re[b]*ci+im[b]*cr;re[b]=re[a]-tr;im[b]=im[a]-ti;re[a]+=tr;im[a]+=ti;const nr=cr*wr-ci*wi;ci=cr*wi+ci*wr;cr=nr}}}
  if(inv)for(let i=0;i<n;i++){re[i]/=n;im[i]/=n}
}
/* r[k] = sum a[n]*b[n+k], for k in [kmin,kmax]; returns {lag, val, conf} of the best peak (with sub-sample refinement) */
function xcorrPeak(a,b,kmin,kmax){
  let N=1;while(N<a.length+b.length)N<<=1;
  const ar=new Float64Array(N),ai=new Float64Array(N),br=new Float64Array(N),bi=new Float64Array(N);
  ar.set(a);br.set(b);fftInPlace(ar,ai,false);fftInPlace(br,bi,false);
  for(let i=0;i<N;i++){const r=ar[i]*br[i]+ai[i]*bi[i],im=ar[i]*bi[i]-ai[i]*br[i];ar[i]=r;ai[i]=im}
  fftInPlace(ar,ai,true);
  const at=k=>ar[(k%N+N)%N];
  let best=-Infinity,bk=0,sum=0,sum2=0,cnt=0;
  for(let k=kmin;k<=kmax;k++){const v=at(k);sum+=v;sum2+=v*v;cnt++;if(v>best){best=v;bk=k}}
  const mean=sum/cnt,sd=Math.sqrt(Math.max(1e-12,sum2/cnt-mean*mean));
  const y0=at(bk-1),y1=best,y2=at(bk+1),den=y0-2*y1+y2;const frac=den!==0?0.5*(y0-y2)/den:0;
  return {lag:bk+Math.max(-1,Math.min(1,frac)),val:best,conf:(best-mean)/sd};
}
function envelope(x,rate,hop){
  hop=hop||Math.round(rate/100);const n=Math.floor(x.length/hop),e=new Float32Array(n);
  for(let i=0;i<n;i++){let s=0;const o=i*hop;for(let j=0;j<hop;j++){const v=x[o+j];s+=v*v}e[i]=Math.log(1e-6+Math.sqrt(s/hop))}
  // onset strength: positive change vs. half-second local mean
  const out=new Float32Array(n);let acc=0;const W=50;
  for(let i=0;i<n;i++){acc+=e[i];if(i>=W)acc-=e[i-W];const m=acc/Math.min(i+1,W);out[i]=Math.max(0,e[i]-m)}
  return out;
}
function window16(x,rate,startSec,lenSec){const s=Math.max(0,Math.round(startSec*rate)),e=Math.min(x.length,s+Math.round(lenSec*rate));return {x:x.subarray(s,e),start:s/rate}}
/* audio time = a + b * video time */
async function computeSync(onStatus){
  const V=STU.wavV,A=STU.wavA;if(!V||!A)throw new Error('Load both prepared sound files first.');
  onStatus('Finding the rough offset…');await sleep(30);
  const ev=envelope(V.x,V.rate),ea=envelope(A.x,A.rate);
  const coarse=xcorrPeak(ev,ea,-(ev.length-1),ea.length-1);
  const a0=coarse.lag/100;
  const durV=V.x.length/V.rate;
  const fine=async(tv)=>{onStatus(`Fine-tuning at ${fmtT(tv)}…`);await sleep(20);
    const w=window16(V.x,V.rate,tv-10,20);const wa=window16(A.x,A.rate,w.start+a0-1.5,23);
    const r=xcorrPeak(w.x,wa.x,-Math.round(3*A.rate),Math.round(3*A.rate));
    return {tv:w.start,off:wa.start+r.lag/A.rate-w.start,conf:r.conf}};
  const t1=Math.max(10,Math.min(durV*0.12,durV/2-10)),t2=Math.max(t1+5,Math.min(durV-30,durV*0.88));
  const p1=await fine(t1),p2=durV>60?await fine(t2):p1;
  let b=1,a=p1.off;
  if(p2!==p1&&p2.tv>p1.tv){const slope=(p2.off-p1.off)/(p2.tv-p1.tv);if(Math.abs(slope)<0.0008){b=1+slope;a=p1.off-slope*p1.tv}}
  const conf=Math.min(coarse.conf,p1.conf,p2.conf);
  return {a,b,conf,coarseConf:coarse.conf,points:[p1,p2]};
}

/* ---------- transcription ---------- */
function wavBlob(x,rate,from,to){
  const n=to-from,buf=new ArrayBuffer(44+n*2),dv=new DataView(buf);const w=(o,s)=>{for(let i=0;i<s.length;i++)dv.setUint8(o+i,s.charCodeAt(i))};
  w(0,'RIFF');dv.setUint32(4,36+n*2,true);w(8,'WAVE');w(12,'fmt ');dv.setUint32(16,16,true);dv.setUint16(20,1,true);dv.setUint16(22,1,true);
  dv.setUint32(24,rate,true);dv.setUint32(28,rate*2,true);dv.setUint16(32,2,true);dv.setUint16(34,16,true);w(36,'data');dv.setUint32(40,n*2,true);
  for(let i=0;i<n;i++){const v=Math.max(-1,Math.min(1,x[from+i]));dv.setInt16(44+i*2,v<0?v*32768:v*32767,true)}
  return new Blob([buf],{type:'audio/wav'});
}
function chunkPoints(x,rate,maxSec){
  const pts=[0];let pos=0;const total=x.length;
  while(total-pos>maxSec*rate){
    const target=pos+Math.round((maxSec-8)*rate),lo=target-Math.round(6*rate),hi=Math.min(total-1,target+Math.round(6*rate));
    let best=target,bestE=Infinity;const win=Math.round(0.2*rate);
    for(let p=lo;p+win<hi;p+=Math.round(0.05*rate)){let e=0;for(let j=0;j<win;j+=4)e+=x[p+j]*x[p+j];if(e<bestE){bestE=e;best=p+win/2|0}}
    pts.push(best);pos=best;
  }
  pts.push(total);return pts;
}
async function groqWords(blob,lang,signal){
  const fd=new FormData();fd.append('file',blob,'chunk.wav');fd.append('model','whisper-large-v3');fd.append('response_format','verbose_json');
  fd.append('timestamp_granularities[]','word');fd.append('timestamp_granularities[]','segment');fd.append('temperature','0');if(lang)fd.append('language',lang);
  const r=await fetch(PROVIDERS.groq.url+'/audio/transcriptions',{method:'POST',signal,headers:{'Authorization':'Bearer '+pKey('groq')},body:fd});
  const d=await r.json().catch(()=>({}));if(!r.ok)throw {code:r.status,message:(d.error&&d.error.message)||r.statusText,retryAfter:+(r.headers.get('retry-after')||0)};
  const words=(d.words||[]).map(w=>({w:String(w.word||'').trim(),s:+w.start,e:+w.end})).filter(w=>w.w);
  // put punctuation back from segment text
  for(const sg of d.segments||[]){const toks=String(sg.text||'').trim().split(/\s+/).filter(Boolean);
    const inSeg=words.filter(w=>w.s>=sg.start-0.05&&w.s<sg.end+0.05);
    if(toks.length===inSeg.length) inSeg.forEach((w,i)=>{if(toks[i].replace(/[^\p{L}\p{N}']/gu,'').toLowerCase()===w.w.replace(/[^\p{L}\p{N}']/gu,'').toLowerCase())w.w=toks[i]})}
  if(!words.length&&d.segments) for(const sg of d.segments){const toks=String(sg.text||'').trim().split(/\s+/).filter(Boolean),dt=(sg.end-sg.start)/Math.max(1,toks.length);toks.forEach((t,i)=>words.push({w:t,s:sg.start+i*dt,e:sg.start+(i+1)*dt}))}
  return words;
}
async function geminiWords(blob,lang,signal){
  const b64=await blobToBase64(blob);
  const txt=await gemini([{inline_data:{mime_type:'audio/wav',data:b64}},{text:`Transcribe this audio word for word${lang?` (language: ${lang})`:''}. Do not summarise or correct. Reply with only JSON: {"segments":[{"start":0.0,"end":4.2,"text":"..."}]} using seconds from the start of this clip, one segment per sentence.`}],{json:true,signal,maxTokens:60000});
  const d=parseJSON(txt);const out=[];
  for(const sg of (d.segments||d||[])){const toks=String(sg.text||'').trim().split(/\s+/).filter(Boolean),s=+sg.start,e=+sg.end;if(!toks.length||!(e>s))continue;const dt=(e-s)/toks.length;toks.forEach((t,i)=>out.push({w:t,s:s+i*dt,e:s+(i+1)*dt}))}
  return out;
}
async function transcribeStudio(onStatus,signal){
  if(STU.settings.asr!=='online'&&typeof ENG!=='undefined'&&ENG.on&&STU.engPrep){  // on this computer (Studio Engine)
    onStatus('Transcribing on this computer (see progress at the top)…');
    const r=await engRun('transcribe',engParams({quality:STU.settings.asr||'accurate',language:STU.settings.lang||''}));
    const SP=stuSpan();STU.words=r.words.map(w=>({w:w.w,s:w.s,e:w.e,...(w.p<0.5?{u:true}:{})})).filter(w=>w.e>-SP.pre&&w.s<STU.dur+SP.post+1);
    STU.asrWords=STU.words.map(w=>({...w}));STU.aligned=false;if(typeof ANA!=='undefined'&&ANA)anaMergeSounds();
    STU.removed={};STU.transcribed=true;autoMarks();await stuSave();onStatus(r.message);return;
  }
  const src=STU.settings.separate&&STU.wavA?STU.wavA:STU.wavV;if(!src)throw new Error('Load the prepared sound files first.');
  const useGroq=!!pKey('groq')&&!(cooled.groq>Date.now());const useGem=!!pKey('gemini');
  if(!useGroq&&!useGem)throw new Error('Add a Groq key (best, free) or a Gemini key in Setup first.');
  const pts=chunkPoints(src.x,src.rate,useGroq?590:290);const all=[];
  for(let i=0;i<pts.length-1;i++){
    const from=pts[i],to=pts[i+1],off=from/src.rate;const blob=wavBlob(src.x,src.rate,from,to);
    let words=null,tries=0;
    while(!words){
      onStatus(`Transcribing part ${i+1} of ${pts.length-1}${useGroq?' with Groq Whisper':' with Gemini'}…`);
      try{words=useGroq?await groqWords(blob,STU.settings.lang,signal):await geminiWords(blob,STU.settings.lang,signal)}
      catch(e){if(e&&e.name==='AbortError')throw e;tries++;
        if(e&&e.code===429&&tries<6){const w=Math.min(90,e.retryAfter||30);for(let s=w;s>0;s--){onStatus(`Free-tier limit — continuing in ${s}s…`);await sleep(1000)}continue}
        if(useGroq&&useGem&&tries>=2){onStatus('Groq failed — using Gemini for this part…');try{words=await geminiWords(blob,STU.settings.lang,signal);break}catch(e2){throw e2}}
        if(tries>=3)throw e;await sleep(3000)}
    }
    for(const w of words)all.push({w:w.w,s:w.s+off,e:w.e+off});
  }
  // to video timeline
  const {a,b}=STU.sync||{a:0,b:1};const map=t=>(STU.settings.separate&&STU.wavA)?(t-a)/b:t;
  STU.words=all.map(w=>({w:w.w,s:map(w.s),e:map(w.e)})).filter(w=>w.e>0&&w.s<STU.dur+1);
  STU.asrWords=STU.words.map(w=>({...w}));STU.aligned=false;if(typeof ANA!=='undefined'&&ANA)anaMergeSounds();
  STU.removed={};STU.transcribed=true;autoMarks();await stuSave();
}

/* ---------- edit model ---------- */
const isFiller=w=>FILLERS.has(w.w.toLowerCase().replace(/[^a-z]/g,''));
function autoMarks(){
  for(const k of Object.keys(STU.removed))if(STU.removed[k]==='auto')delete STU.removed[k];
  if(STU.settings.fillers)STU.words.forEach((w,i)=>{if(isFiller(w)&&!STU.removed[i])STU.removed[i]='auto'});
  if(STU.settings.extraCut!==false)STU.words.forEach((w,i)=>{if(w.x&&!STU.removed[i])STU.removed[i]='auto'});
}
/* 'keep the whole recording': seconds of recorder before the camera started (pre) and after it stopped (post) */
function stuSpan(){
  const s=STU.sync;if(!(STU.settings.keepAll&&STU.settings.separate&&s&&s.audio_dur&&STU.dur))return {pre:0,post:0};
  const pre=Math.max(0,s.a/s.b),post=Math.max(0,(s.audio_dur-s.a)/s.b-STU.dur);
  return {pre:pre>0.25?+pre.toFixed(3):0,post:post>0.25?+post.toFixed(3):0};
}
function keepSegments(){
  const W=STU.words,SP=stuSpan(),T0=-SP.pre,D=(STU.dur||((W.length?W[W.length-1].e:0)+1))+SP.post,cuts=[];
  const q=(a,b)=>(typeof quietest==='function'?quietest(a,b):(a+b)/2);
  const gapMid=(i)=>{const p=W[i-1],n=W[i];return p?q(Math.min(p.e,n.s),Math.max(p.e,n.s)):Math.max(0,n.s-0.15)};
  for(let i=0;i<W.length;i++){if(!STU.removed[i])continue;let j=i;while(j+1<W.length&&STU.removed[j+1])j++;
    const s=i>0?Math.min(gapMid(i),W[i].s):Math.max(T0,W[i].s-0.1),e=j+1<W.length?Math.max(q(Math.min(W[j].e,W[j+1].s),Math.max(W[j].e,W[j+1].s)),W[j].e):Math.min(D,W[j].e+0.15);cuts.push([s,e]);i=j}
  for(const c of STU.manualCuts||[])if(c[1]>c[0])cuts.push([Math.max(T0,c[0]),Math.min(D,c[1])]);  // cut by hand in the Editor's timeline
  if(STU.settings.pauses&&typeof ANA!=='undefined'&&ANA)cuts.push(...anaPauseCuts(Math.max(0.4,+STU.settings.maxPause||1),STU.settings.keepBreaths!==false));
  else if(STU.settings.pauses){const mp=Math.max(0.4,+STU.settings.maxPause||1);let prev=null;
    for(let i=0;i<W.length;i++){if(STU.removed[i])continue;if(prev!==null){const g=W[i].s-W[prev].e;if(g>mp)cuts.push([W[prev].e+mp/2,W[i].s-mp/2])}prev=i}}
  if(STU.settings.trimEnds&&W.length){const first=W.findIndex((w,i)=>!STU.removed[i]);let last=W.length-1;while(last>=0&&STU.removed[last])last--;
    if(first>=0){if(W[first].s-T0>0.8)cuts.push([T0,W[first].s-0.5]);if(D-W[last].e>0.8)cuts.push([W[last].e+0.5,D])}}
  cuts.sort((x,y)=>x[0]-y[0]);const merged=[];
  for(const c of cuts){if(c[1]-c[0]<0.08)continue;const m=merged[merged.length-1];if(m&&c[0]<=m[1]+0.05)m[1]=Math.max(m[1],c[1]);else merged.push([...c])}
  const keep=[];let t=T0;for(const c of merged){if(c[0]-t>0.2)keep.push([t,c[0]]);t=Math.max(t,c[1])}if(D-t>0.2)keep.push([t,D]);
  return keep;
}
function clipSegs(segs,s,e){const out=[];for(const [a,b] of segs){const x=Math.max(a,s),y=Math.min(b,e);if(y-x>0.15)out.push([x,y])}return out}
function mapper(segs){const starts=[];let acc=0;for(const [a,b] of segs){starts.push(acc);acc+=b-a}
  return {total:acc,map:t=>{for(let i=0;i<segs.length;i++){if(t>=segs[i][0]-1e-6&&t<=segs[i][1]+1e-6)return starts[i]+(t-segs[i][0])}return null}}}
function captionChunks(segs,maxWords,maxChars){
  const m=mapper(segs),out=[];let cur=[];
  const flush=()=>{if(!cur.length)return;const s=m.map(cur[0].s),e=m.map(cur[cur.length-1].e);if(s!=null&&e!=null&&e>s)out.push({s,e:Math.max(e,s+0.8),t:cur.map(w=>w.w).join(' ')});cur=[]};
  STU.words.forEach((w,i)=>{if(STU.removed[i]||w.snd)return;const ms=m.map(w.s),me=m.map(w.e);if(ms==null||me==null){flush();return}
    const prev=cur[cur.length-1];if(prev&&(w.s-prev.e>0.7||cur.length>=maxWords||(cur.map(x=>x.w).join(' ').length+w.w.length)>maxChars))flush();
    cur.push(w);if(/[.?!]$/.test(w.w))flush()});
  flush();
  // let each caption stay up to 0.7 s longer into the following pause (easier to read), never overlapping
  for(let i=0;i<out.length;i++){const next=i+1<out.length?out[i+1].s:Infinity;out[i].e=Math.min(next,out[i].e+0.7)}
  for(let i=0;i<out.length-1;i++)if(out[i].e>out[i+1].s)out[i].e=out[i+1].s;return out;
}
const srtT=t=>{const ms=Math.round(t*1000),h=Math.floor(ms/3600000),m=Math.floor(ms%3600000/60000),s=Math.floor(ms%60000/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')},${String(ms%1000).padStart(3,'0')}`};
/* at most 42 characters per line, 2 lines (broadcast readability rules); split near the middle */
function wrap2(t){if(t.length<=42)return t;const w=t.split(' ');let best=null;
  for(let k=1;k<w.length;k++){const a=w.slice(0,k).join(' '),b=w.slice(k).join(' ');const sc=Math.max(a.length,b.length);if(!best||sc<best.sc)best={a,b,sc}}
  return best?best.a+'\n'+best.b:t}
function makeSrt(segs){return captionChunks(segs,10,60).map((c,i)=>`${i+1}\n${srtT(c.s)} --> ${srtT(c.e)}\n${wrap2(c.t)}\n`).join('\n')}
const assT=t=>{const cs=Math.round(t*100),h=Math.floor(cs/360000),m=Math.floor(cs%360000/6000),s=Math.floor(cs%6000/100);return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(cs%100).padStart(2,'0')}`};
function makeAss(segs){
  const head=`[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,74,&H00FFFFFF,&H00FFFFFF,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,5,2,2,90,90,560,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n`;
  return head+captionChunks(segs,5,28).map(c=>`Dialogue: 0,${assT(c.s)},${assT(c.e)},Default,,0,0,0,,${c.t.replace(/[{}\\]/g,'')}`).join('\n')+'\n';
}

/* ---------- scripts for the user's computer ---------- */
const OUT='studio_out';
function isHdr(){const v=(STU.info&&STU.info.streams||[]).find(s=>s.codec_type==='video');return !!(v&&/arib-std-b67|smpte2084/.test(v.color_transfer||''))}
function videoFps(){const v=(STU.info&&STU.info.streams||[]).find(s=>s.codec_type==='video');const r=(v&&(v.avg_frame_rate||v.r_frame_rate)||'30/1').split('/');const f=(+r[0])/(+r[1]||1);return f>0&&f<121?(Math.abs(f-Math.round(f))<0.02?Math.round(f):+f.toFixed(3)):30}
// fixed processing delay of the denoisers (measured): afftdn 25 ms, arnndn 10 ms — removed again so lips stay in sync
function cleanLatency(){const p=STU.settings.clean;return p==='strong'?0.035:(p==='light'||p==='standard')?0.025:0}
function cleanChain(){
  const L=cleanLatency();return cleanChainRaw()+(L?`,atrim=start=${L},asetpts=PTS-STARTPTS`:'');
}
function cleanChainRaw(){
  const p=STU.settings.clean;const tail='acompressor=threshold=-21dB:ratio=3:attack=15:release=250:makeup=2,loudnorm=I=-16:TP=-1.5:LRA=11';
  if(p==='light')return 'highpass=f=70,afftdn=nr=8:nf=-45:tn=1,loudnorm=I=-16:TP=-1.5:LRA=11';
  if(p==='strong')return `highpass=f=85,arnndn=m=${OUT}/voice.rnnn:mix=0.85,afftdn=nr=10:nf=-40:tn=1,deesser=i=0.4,${tail}`;
  if(p==='none')return 'loudnorm=I=-16:TP=-1.5:LRA=11';
  return `highpass=f=80,lowpass=f=16000,afftdn=nr=14:nf=-38:tn=1,deesser=i=0.3,${tail}`;
}
function syncChain(){
  if(!(STU.settings.separate&&STU.audioName&&STU.sync))return '';
  const {a,b}=STU.sync||{a:0,b:1};const parts=[];
  if(a>=0)parts.push(`atrim=start=${fx(a)},asetpts=PTS-STARTPTS`);else parts.push(`adelay=${Math.round(-a*1000)}:all=1`);
  if(Math.abs(b-1)>2e-6)parts.push(`aresample=48000,asetrate=${Math.round(48000*b)},aresample=48000`); // exact speed correction (pitch change far below hearing)
  return parts.join(',')+',';
}
function vcodec(kind){
  const e=STU.settings.enc,full=kind==='full';
  if(e==='mac')return `-c:v h264_videotoolbox -b:v ${full?'40M':'12M'} -allow_sw 1`;
  if(e==='nvidia')return `-c:v h264_nvenc -preset p5 -rc vbr -cq ${full?20:19} -b:v 0`;
  if(e==='intel')return `-c:v h264_qsv -global_quality ${full?22:21}`;
  if(e==='amd')return `-c:v h264_amf -quality quality -rc cqp -qp_i ${full?20:19} -qp_p ${full?22:21}`;
  return `-c:v libx264 -preset ${full?'veryfast':'medium'} -crf ${full?20:19}`;
}
function vfilter(kind){
  const f=[];if(isHdr())f.push('zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv');
  if(kind==='reel'){const h=STU.settings.reelRes==='720'?1280:1920,w=h*9/16;f.push(`scale=${w}:${h}:force_original_aspect_ratio=increase,crop=${w}:${h}`)}
  else if(STU.settings.res!=='orig'){const s=+STU.settings.res;f.push(`scale=w='if(gt(iw,ih),-2,${s})':h='if(gt(iw,ih),${s},-2)'`)}
  f.push('format=yuv420p');return f.join(',');
}
function safeName(t){return String(t||'').replace(/[\\/:*?"<>|%!^&$`]/g,'').replace(/\s+/g,' ').trim().slice(0,60)||'Reel'}
function buildScript(os,kind){
  const win=os==='win',L=[],q=s=>`"${s}"`,P=s=>win?s.replace(/\//g,'\\'):s;
  const V=STU.videoName,A=(STU.settings.separate&&STU.audioName)?STU.audioName:STU.videoName;
  const say=t=>L.push(win?`echo ${t.replace(/[<>|&]/g,'')}`:`echo "${t.replace(/"/g,'')}"`);
  const mk=d=>L.push(win?`if not exist ${P(d)} mkdir ${P(d)}`:`mkdir -p "${d}"`);
  const ff='ffmpeg -hide_banner -loglevel error -stats -y';
  if(win){L.push('@echo off','chcp 65001 >nul','cd /d "%~dp0"','where ffmpeg >nul 2>nul || (echo ffmpeg is not installed. Open the Studio tab and follow "Install ffmpeg". & pause & exit /b 1)')}
  else{L.push('#!/bin/bash','cd "$(dirname "$0")" || exit 1','command -v ffmpeg >/dev/null || { echo "ffmpeg is not installed. Open the Studio tab and follow Install ffmpeg."; read -r -p "Press Enter to close"; exit 1; }','set -e')}
  const Vs=V.replace(/[&()<>|^%!]/g,'');
  if(win){L.push(`if not exist ${q(V)} if exist ${q('..\\'+V)} (`,`  if exist ${OUT} xcopy /e /i /y ${OUT} ..\\${OUT} >nul`,`  if exist *.srt copy /y *.srt .. >nul`,`  cd ..`,`)`);
    L.push(`if not exist ${q(V)} (`,`  echo Could not find ${Vs} here. Put these files in the same folder as your video.`,`  pause`,`  exit /b 1`,`)`)}
  else{L.push(`if [ ! -f ${q(V)} ] && [ -f ${q('../'+V)} ]; then mkdir -p ../${OUT}; [ -d ${OUT} ] && cp -R ${OUT}/. ../${OUT}/; cp ./*.srt .. 2>/dev/null || true; cd ..; fi`);
    L.push(`[ -f ${q(V)} ] || { echo "Could not find ${V} here. Put these files in the same folder as your video."; read -r -p "Press Enter"; exit 1; }`)}
  mk(OUT);
  if(kind==='prepare'){
    say('Step 1 of 2: reading the sound from your video (a few minutes for a long video)...');
    L.push(`${ff} -i ${q(V)} -vn -ac 1 -ar 16000 -c:a pcm_s16le ${q(OUT+'/video_16k.wav')}`);
    if(STU.settings.separate&&STU.audioName){L.push(win?`if not exist ${q(A)} (echo Could not find ${A.replace(/[&()<>|^%!]/g,'')} & pause & exit /b 1)`:`[ -f ${q(A)} ] || { echo "Could not find ${A}"; exit 1; }`);
      say('Step 2 of 2: reading your separate audio...');L.push(`${ff} -i ${q(A)} -vn -ac 1 -ar 16000 -c:a pcm_s16le ${q(OUT+'/audio_16k.wav')}`)}
    L.push(`ffprobe -v error -show_streams -show_format -of json ${q(V)} > ${q(P(OUT+'/video_info.json'))}`);
    if(STU.settings.proxy){say('Making a small preview copy (this can take a while)...');L.push(`${ff} -i ${q(V)} -vf "scale=w='if(gt(iw,ih),-2,540)':h='if(gt(iw,ih),540,-2)',format=yuv420p" -c:v libx264 -preset veryfast -crf 30 -c:a aac -b:a 96k ${q(OUT+'/preview.mp4')}`)}
    say('Done! In the Studio tab press "Load prepared files" and choose the files in the studio_out folder.');
  }else{
    const keep=keepSegments();if(!keep.length)throw new Error('Nothing left to render — everything is cut.');
    const fps=videoFps(),afade=d=>`afade=t=in:d=0.012,afade=t=out:st=${fx(Math.max(0,d-0.012))}:d=0.012`;
    if(STU.settings.clean==='strong'){L.push(win?`if not exist ${P(OUT+'/voice.rnnn')} curl -sL -o ${P(OUT+'/voice.rnnn')} https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn`:`[ -f "${OUT}/voice.rnnn" ] || curl -sL -o "${OUT}/voice.rnnn" https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn`)}
    say('Cleaning and syncing the audio...');
    L.push(`${ff} -i ${q(A)} -vn -af "${syncChain()}${cleanChain()}" -ar 48000 -ac 2 -c:a pcm_s16le ${q(OUT+'/clean_audio.wav')}`);
    const renderSet=(name,segs,kindV,label)=>{
      const dir=`${OUT}/${name}`;mk(dir);const list=[];
      segs.forEach(([s,e],i)=>{const d=e-s,f=`${dir}/part_${String(i+1).padStart(4,'0')}.mp4`;list.push(`file 'part_${String(i+1).padStart(4,'0')}.mp4'`);
        say(`${label}: part ${i+1} of ${segs.length}`);
        L.push(`${ff} -ss ${fx(s)} -i ${q(V)} -ss ${fx(s)} -i ${q(OUT+'/clean_audio.wav')} -t ${fx(d)} -map 0:v:0 -map 1:a:0 -vf "${vfilter(kindV)}" -r ${fps} ${vcodec(kindV)} -af "${afade(d)}" -c:a aac -b:a 192k -ar 48000 ${q(f)}`)});
      const listFile=`${dir}/list.txt`;
      if(win){L.push(`type nul > ${q(P(listFile))}`);list.forEach(l=>L.push(`echo ${l}>> ${q(P(listFile))}`))}else{L.push(`cat > ${q(listFile)} <<'LIST'`,...list,'LIST')}
      return listFile;
    };
    // full edited video + podcast audio
    say('Making the full edited video...');
    const fullList=renderSet('full',keep,'full','Full video');
    L.push(`${ff} -f concat -safe 0 -i ${q(fullList)} -c copy -movflags +faststart ${q('Edited - full video.mp4')}`);
    say('Making the full edited audio (podcast)...');
    L.push(`${ff} -i ${q('Edited - full video.mp4')} -vn -c:a libmp3lame -b:a 192k ${q('Edited - full audio.mp3')}`);
    // reels
    STU.reels.forEach((r,ri)=>{
      const segs=clipSegs(keep,r.start,r.end);if(!segs.length)return;const nm=`Reel ${String(ri+1).padStart(2,'0')} - ${safeName(r.title)}`;
      say(`Making ${nm}...`);const lf=renderSet(`reel_${ri+1}`,segs,'reel',`Reel ${ri+1}`);
      if(STU.settings.reelCaps){L.push(`${ff} -f concat -safe 0 -i ${q(lf)} -c copy ${q(OUT+`/reel_${ri+1}_raw.mp4`)}`);
        L.push(`${ff} -i ${q(OUT+`/reel_${ri+1}_raw.mp4`)} -vf "subtitles=${OUT}/reel_${ri+1}.ass" ${vcodec('reel')} -c:a copy -movflags +faststart ${q(nm+'.mp4')}`)}
      else L.push(`${ff} -f concat -safe 0 -i ${q(lf)} -c copy -movflags +faststart ${q(nm+'.mp4')}`);
    });
    say('All done! Your files are in this folder.');
  }
  if(win){for(let i=0;i<L.length;i++)if(/^(ffmpeg|ffprobe) /.test(L[i]))L[i]+=' || goto fail';L.push('echo.','pause','exit /b 0',':fail','echo.','echo Something went wrong above. Please send a screenshot of this window.','pause','exit /b 1')}
  else L.push('read -r -p "Press Enter to close"');
  return L.join(win?'\r\n':'\n')+(win?'\r\n':'\n');
}
async function downloadRender(os){
  const keep=keepSegments();const files=[];
  files.push({name:os==='win'?'2-render.bat':'2-render.command',data:buildScript(os,'render')});
  if(STU.settings.reelCaps)STU.reels.forEach((r,i)=>{const segs=clipSegs(keep,r.start,r.end);if(segs.length)files.push({name:`${OUT}/reel_${i+1}.ass`,data:makeAss(segs)})});
  files.push({name:'Edited - full video.srt',data:makeSrt(keep)});
  if(!window.JSZip){throw new Error('The zip tool did not load. Refresh and try again.')}
  const zip=new JSZip();files.forEach(f=>zip.file(f.name,f.data));
  zip.file('README - how to render.txt',`1. Unzip into the SAME folder as ${STU.videoName}.\n2. ${os==='win'?'Double-click 2-render.bat':'Open Terminal, type "bash " (with a space), drag 2-render.command into the window and press Enter'}.\n3. Leave it running. When it says "All done", your edited video, podcast audio and reels are in that folder.\n`);
  saveBlob(await zip.generateAsync({type:'blob'}),`studio-render-${os==='win'?'windows':'mac'}.zip`);
}

/* ---------- UI ---------- */
function stuRenderInfo(){
  const v=(STU.info&&STU.info.streams||[]).find(s=>s.codec_type==='video');
  const parts=[];if(STU.videoName)parts.push(`Video: ${STU.videoName}`);if(STU.settings.separate&&STU.audioName)parts.push(`Audio: ${STU.audioName}`);
  if(v)parts.push(`${v.width}×${v.height}, ${videoFps()} fps, ${v.codec_name}${isHdr()?', HDR (will be converted for normal screens)':''}`);
  if(STU.dur)parts.push(`length ${fmtT(STU.dur)}`);
  $('#stuInfo').textContent=parts.join(' · ');
  $('#stuAudioRow').hidden=!STU.settings.separate;
  const s=STU.sync;$('#stuSyncOut').textContent=s?(s.engine&&s.message?s.message:`Offset ${(s.a*1000).toFixed(0)} ms, drift ${((s.b-1)*1e6).toFixed(0)} ppm${s.conf<4?' — low confidence: check lip sync and adjust below':' — confident match'}`):'';
  if(s)$('#stuOffset').value=Math.round(s.a*1000);
  ['clean','res','enc','reelRes','lang','asr'].forEach(k=>{const el=$('#stu_'+k);if(el)el.value=STU.settings[k]});
  ['fillers','pauses','trimEnds','reelCaps','proxy','separate'].forEach(k=>{const el=$('#stu_'+k);if(el)el.checked=!!STU.settings[k]});
  const ex=STU.settings.extras||[];if($('#stu_exWhatsapp')){$('#stu_exWhatsapp').checked=ex.includes('whatsapp');$('#stu_exSquare').checked=ex.includes('square')}
  const el=$('#stu_maxPause');if(el)el.value=STU.settings.maxPause;
  // keep the whole recording
  const kb=$('#stuKeepAllBox');if(kb){kb.hidden=!STU.settings.separate;$('#stu_keepAll').checked=!!STU.settings.keepAll;
    $('#stu_fill').value=STU.settings.fill||'auto';$('#stu_fillTitle').value=STU.settings.fillTitle||'';
    $('#stuFillTitleRow').hidden=STU.settings.fill==='file';$('#stuFillFileRow').hidden=STU.settings.fill!=='file';
    $('#stuFillFile').textContent=STU.settings.fillFile?STU.settings.fillFile.split(/[\\/]/).pop():'nothing chosen';
    const SP=stuSpan(),raw=STU.sync&&STU.sync.audio_dur?{pre:Math.max(0,STU.sync.a/STU.sync.b),post:Math.max(0,(STU.sync.audio_dur-STU.sync.a)/STU.sync.b-STU.dur)}:null;
    $('#stuSpanInfo').textContent=!raw?'Find the sync first: then you will see how much of the recording is outside the video.':
      (raw.pre>0.25||raw.post>0.25)?`The recorder started ${raw.pre.toFixed(1)} s before the camera${raw.post>0.25?` and ran ${raw.post.toFixed(1)} s after it stopped`:''}. `+(STU.settings.keepAll?'That sound is kept; the picture there is filled automatically.':'Tick the box to keep that sound too.'):'The recording is all inside the video — nothing extra to keep.'}
}
function stuRenderTranscript(){
  const box=$('#stuText');if(!STU.words.length){box.innerHTML='<p class="status">No transcript yet.</p>';stuStats();return}
  let html='',para=[];const flush=()=>{if(para.length)html+=`<p><span class="stu-time">${fmtT(STU.words[para[0]].s)}</span> ${para.map(i=>`<span data-i="${i}" class="stu-w${STU.removed[i]?' cut':''}${isFiller(STU.words[i])?' filler':''}${STU.words[i].u?' unsure':''}${STU.words[i].x?' extra':''}">${esc(STU.words[i].w)}</span>`).join(' ')}</p>`;para=[]};
  STU.words.forEach((w,i)=>{const prev=STU.words[i-1];if(prev&&(w.s-prev.e>1.5||(para.length>45&&/[.?!]$/.test(prev.w))))flush();para.push(i)});flush();
  box.innerHTML=html;stuStats();
}
function stuStats(){
  if(!STU.words.length){$('#stuStats').textContent='';return}
  const keep=keepSegments(),tot=keep.reduce((a,[s,e])=>a+e-s,0);
  $('#stuStats').textContent=`Edited length ${fmtT(tot)} (from ${fmtT(STU.dur)}) · ${keep.length} parts · ${Object.keys(STU.removed).length} words removed`;
  $('#stuReels').innerHTML=STU.reels.map((r,i)=>{const segs=clipSegs(keep,r.start,r.end),d=segs.reduce((a,[s,e])=>a+e-s,0);
    return `<div class="item"><div class="row"><label class="f" style="margin:0"><span>Reel ${i+1} title</span><input data-rtitle="${i}" value="${esc(r.title||'')}"></label>
      <div class="status" style="align-self:end">${fmtT(r.start)} – ${fmtT(r.end)} · ${d.toFixed(0)} s${d>90?' (long for a Reel)':''}</div></div>
      ${r.why?`<p class="hint" style="margin:6px 0 0">${esc(r.why)}</p>`:''}
      <div class="acts" style="margin-top:6px"><button class="btn small" data-rplay="${i}">▶ Play</button><button class="btn small" data-rdel="${i}">Remove</button></div></div>`}).join('')||'<p class="status">No reels yet.</p>';
}
function selectedRange(){
  const sel=window.getSelection();if(!sel||sel.isCollapsed)return null;
  const a=(sel.anchorNode&&(sel.anchorNode.nodeType===1?sel.anchorNode:sel.anchorNode.parentElement)).closest('[data-i]'),b=(sel.focusNode&&(sel.focusNode.nodeType===1?sel.focusNode:sel.focusNode.parentElement)).closest('[data-i]');
  if(!a||!b)return null;const x=+a.dataset.i,y=+b.dataset.i;return [Math.min(x,y),Math.max(x,y)];
}
let stuCtl=null,playEdited=false;
function stuVideo(){return $('#stuVideo')}
function wireStudio(){
  $('#stu_separate').addEventListener('change',e=>{STU.settings.separate=e.target.checked;stuSave();stuRenderInfo()});
  $('#stuVideoFile').addEventListener('change',e=>{const f=e.target.files[0];if(!f)return;STU.videoName=f.name;STU.videoFile=f;stuVideo().src=URL.createObjectURL(f);stuSave();stuRenderInfo()});
  $('#stuAudioFile').addEventListener('change',e=>{const f=e.target.files[0];if(!f)return;STU.audioName=f.name;stuSave();stuRenderInfo()});
  stuVideo().addEventListener('error',()=>stuSay('#stuPrepStatus',(typeof ENG!=='undefined'&&ENG.on)?'This browser cannot play the preview video (H.264). Please use Chrome or Edge for the Studio tab.':'Your browser cannot play this video directly. Tick "Make a small preview copy", download the prepare file again and run it, then load preview.mp4.'));
  stuVideo().addEventListener('loadedmetadata',()=>{if(!STU.dur&&isFinite(stuVideo().duration))STU.dur=stuVideo().duration});
  ['clean','res','enc','reelRes','lang','asr'].forEach(k=>$('#stu_'+k).addEventListener('change',e=>{STU.settings[k]=e.target.value;stuSave()}));
  ['fillers','pauses','trimEnds','reelCaps','proxy'].forEach(k=>$('#stu_'+k).addEventListener('change',e=>{STU.settings[k]=e.target.checked;if(k==='fillers')autoMarks();stuSave();stuRenderTranscript()}));
  $('#stu_maxPause').addEventListener('change',e=>{STU.settings.maxPause=+e.target.value||1;stuSave();stuStats()});
  $('#stuPrepWin').addEventListener('click',()=>stuPrep('win'));$('#stuPrepMac').addEventListener('click',()=>stuPrep('mac'));
  $('#stuLoad').addEventListener('change',async e=>{
    const files=[...e.target.files];if(!files.length)return;stuSay('#stuLoadStatus','Reading…');
    try{for(const f of files){const n=f.name.toLowerCase();
      if(n.endsWith('video_16k.wav'))STU.wavV=parseWav(await f.arrayBuffer());
      else if(n.endsWith('audio_16k.wav'))STU.wavA=parseWav(await f.arrayBuffer());
      else if(n.endsWith('.json'))STU.info=JSON.parse(await f.text());
      else if(n.endsWith('preview.mp4')){STU.proxyFile=f;stuVideo().src=URL.createObjectURL(f)}}
      if(STU.wavV)STU.dur=STU.wavV.x.length/STU.wavV.rate;else if(STU.info&&STU.info.format)STU.dur=+STU.info.format.duration;
      const miss=[];if(!STU.wavV)miss.push('video_16k.wav');if(STU.settings.separate&&!STU.wavA)miss.push('audio_16k.wav');if(!STU.info)miss.push('video_info.json');
      stuSay('#stuLoadStatus',miss.length?`Loaded. Still missing: ${miss.join(', ')}.`:'All prepared files loaded.');await stuSave();stuRenderInfo();
    }catch(err){stuSay('#stuLoadStatus',err.message)}e.target.value=''});
  $('#stuSync').addEventListener('click',async()=>{
    try{STU.sync=(ENG.on&&STU.engPrep)?await engSync(t=>stuSay('#stuSyncOut',t)):await computeSync(t=>stuSay('#stuSyncOut',t));await stuSave();stuRenderInfo()}catch(e){stuSay('#stuSyncOut',e.message)}});
  ['exWhatsapp','exSquare'].forEach(k=>$('#stu_'+k).addEventListener('change',()=>{STU.settings.extras=[...($('#stu_exWhatsapp').checked?['whatsapp']:[]),...($('#stu_exSquare').checked?['square']:[])];stuSave()}));
  $('#stu_keepAll').addEventListener('change',e=>{STU.settings.keepAll=e.target.checked;stuSave();stuRenderInfo();stuRenderTranscript()});
  $('#stu_fill').addEventListener('change',e=>{STU.settings.fill=e.target.value;stuSave();stuRenderInfo()});
  $('#stu_fillTitle').addEventListener('change',e=>{STU.settings.fillTitle=e.target.value;stuSave()});
  $('#stuFillPick').addEventListener('click',async()=>{try{const d=await engChoose('cover');if(d.path){STU.settings.fillFile=d.path;stuSave();stuRenderInfo()}}catch(err){alert(err.message)}});
  $('#stuOffset').addEventListener('change',e=>{STU.sync={...(STU.sync||{b:1,conf:99}),a:(+e.target.value||0)/1000,engine:false};stuSave();stuRenderInfo()});
  $('#stuTranscribe').addEventListener('click',async()=>{
    if(STU.settings.separate&&STU.wavA&&!STU.sync){stuSay('#stuTxStatus','Press "Find sync automatically" first.');return}
    if(STU.words.length&&!confirm('Transcribe again? Your current edits will be lost.'))return;
    stuCtl=new AbortController();$('#stuTranscribe').disabled=true;
    try{await transcribeStudio(t=>stuSay('#stuTxStatus',t),stuCtl.signal);stuSay('#stuTxStatus',`Done: ${STU.words.length.toLocaleString()} words.`);stuRenderTranscript()}
    catch(e){stuSay('#stuTxStatus',e.name==='AbortError'?'Stopped.':errMsg(e))}finally{$('#stuTranscribe').disabled=false;stuCtl=null}});
  $('#stuText').addEventListener('click',e=>{const w=e.target.closest('[data-i]');if(!w||!window.getSelection().isCollapsed)return;const v=stuVideo();if(v.src){v.currentTime=Math.max(0,STU.words[+w.dataset.i].s-0.2)}});
  const mark=(val)=>{const r=selectedRange();if(!r){alert('First select some words in the transcript with your mouse.');return}for(let i=r[0];i<=r[1];i++){if(val)STU.removed[i]='user';else delete STU.removed[i]}window.getSelection().removeAllRanges();stuSave();stuRenderTranscript()};
  $('#stuCut').addEventListener('click',()=>mark(true));$('#stuRestore').addEventListener('click',()=>mark(false));
  $('#stuMakeReel').addEventListener('click',()=>{const r=selectedRange();if(!r){alert('Select the words for the reel first.');return}
    STU.reels.push({title:STU.words.slice(r[0],r[0]+6).map(w=>w.w).join(' '),start:Math.max(0,STU.words[r[0]].s-0.15),end:STU.words[r[1]].e+0.25});window.getSelection().removeAllRanges();stuSave();stuStats()});
  $('#stuReels').addEventListener('input',e=>{const i=e.target.dataset.rtitle;if(i!=null){STU.reels[+i].title=e.target.value;stuSave()}});
  $('#stuReels').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.rdel!=null){STU.reels.splice(+b.dataset.rdel,1);stuSave();stuStats()}
    if(b.dataset.rplay!=null){const r=STU.reels[+b.dataset.rplay],v=stuVideo();if(!v.src){alert('Choose your video in step 1 to preview.');return}playEdited=true;$('#stuPlayEdited').checked=true;v.currentTime=r.start;v.play();
      const stop=()=>{if(v.currentTime>=r.end){v.pause();v.removeEventListener('timeupdate',stop)}};v.addEventListener('timeupdate',stop)}});
  $('#stuPlayEdited').addEventListener('change',e=>{playEdited=e.target.checked});
  stuVideo().addEventListener('timeupdate',()=>{if(!playEdited||!STU.words.length)return;const v=stuVideo(),t=v.currentTime,keep=keepSegments();
    if(!keep.some(([s,e])=>t>=s-0.05&&t<e)){const next=keep.find(([s])=>s>t);if(next)v.currentTime=next[0];else v.pause()}});
  $('#stuSuggest').addEventListener('click',stuSuggest);
  $('#stuRenderWin').addEventListener('click',()=>downloadRender('win').catch(e=>alert(e.message)));
  $('#stuRenderMac').addEventListener('click',()=>downloadRender('mac').catch(e=>alert(e.message)));
  $('#stuSrt').addEventListener('click',()=>saveBlob(new Blob([makeSrt(keepSegments())],{type:'text/plain'}),'Edited - full video.srt'));
  $('#stuCsv').addEventListener('click',()=>{const k=keepSegments();saveBlob(new Blob(['part,start,end,length\n'+k.map(([s,e],i)=>`${i+1},${fmtT(s)},${fmtT(e)},${(e-s).toFixed(2)}`).join('\n')],{type:'text/csv'}),'cut-list.csv')});
  $('#stuText2').addEventListener('click',()=>{const k=keepSegments(),m=mapper(k);saveBlob(new Blob([STU.words.filter((w,i)=>!STU.removed[i]&&!w.snd).map(w=>w.w).join(' ')],{type:'text/plain'}),'Edited transcript.txt')});
  $('#stuSaveProj').addEventListener('click',()=>{const {videoFile,proxyFile,wavV,wavA,...rest}=STU;saveBlob(new Blob([JSON.stringify(rest)],{type:'application/json'}),`studio-project-${todayStr()}.json`)});
  $('#stuLoadProj').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;try{const d=JSON.parse(await f.text());if(!('words' in d))throw 0;STU={...STU,...d,settings:{...STU_DEFAULTS,...(d.settings||{})}};await stuSave();stuRenderInfo();stuRenderTranscript()}catch(err){alert('That is not a Studio project file.')}e.target.value=''});
  $('#stuStop').addEventListener('click',()=>stuCtl&&stuCtl.abort());
}
async function stuPrep(os){
  if(!STU.videoName){alert('Choose your video file first.');return}
  if(STU.settings.separate&&!STU.audioName){alert('Choose your separate audio file, or untick "I recorded the audio separately".');return}
  const zip=new JSZip();zip.file(os==='win'?'1-prepare.bat':'1-prepare.command',buildScript(os,'prepare'));
  zip.file('README - how to prepare.txt',`1. Unzip into the SAME folder as ${STU.videoName}${STU.settings.separate?` and ${STU.audioName}`:''}.\n2. ${os==='win'?'Double-click 1-prepare.bat':'Open Terminal, type "bash " (with a space), drag 1-prepare.command into the window and press Enter'}.\n3. When it says Done, go back to the Studio tab and press "Load prepared files" and choose everything inside the new studio_out folder.\n`);
  saveBlob(await zip.generateAsync({type:'blob'}),`studio-prepare-${os==='win'?'windows':'mac'}.zip`);
}
async function stuSuggest(){
  if(!STU.words.length){stuSay('#stuReelStatus','Transcribe first.');return}
  const lines=[];let cur=[];STU.words.forEach((w,i)=>{if(STU.removed[i]||w.snd)return;cur.push(i);if(/[.?!]$/.test(w.w)||cur.length>40){lines.push(cur);cur=[]}});if(cur.length)lines.push(cur);
  const text=lines.map((l,k)=>`[${k}] (${fmtT(STU.words[l[0]].s)}) ${l.map(i=>STU.words[i].w).join(' ')}`).join('\n');
  const n=Math.max(1,Math.min(15,+$('#stuReelCount').value||5));
  stuSay('#stuReelStatus','Asking AI for the best moments…');
  try{
    const prompt=`Below is the transcript of a recorded spiritual talk (Pranahuti Yoga / Sahaj Marg tradition), split into numbered lines.
Choose the ${n} best moments for Instagram Reels / YouTube Shorts. Each moment must:
- be 25 to 60 seconds long (lines show their start time),
- start at the beginning of a line and end at the end of a line,
- contain one complete, self-contained thought that makes sense without context,
- ideally open with a strong first sentence (a hook).
Do not choose moments that overlap. Reply with only JSON: {"reels":[{"start_line":12,"end_line":18,"title":"short catchy title","why":"one sentence why this works"}]}

TRANSCRIPT:
${text}`;
    const got=await askAny(prompt,{json:true,onStatus:t=>stuSay('#stuReelStatus',t)});
    let arr=parseJSON(got.text);arr=Array.isArray(arr)?arr:(arr.reels||[]);let added=0;
    for(const r of arr){const a=+r.start_line,b=+r.end_line;if(!(a>=0&&b>=a&&b<lines.length))continue;
      const s=STU.words[lines[a][0]].s-0.15,e=STU.words[lines[b][lines[b].length-1]].e+0.25;if(e-s<10||e-s>120)continue;
      STU.reels.push({title:String(r.title||'Reel').slice(0,80),why:String(r.why||'').slice(0,200),start:Math.max(0,s),end:e});added++}
    stuSave();stuStats();stuSay('#stuReelStatus',`Added ${added} reel suggestion${added===1?'':'s'} (with ${PROVIDERS[got.pid].label}). Play them, rename or remove any.`);
  }catch(e){stuSay('#stuReelStatus',errMsg(e))}
}



