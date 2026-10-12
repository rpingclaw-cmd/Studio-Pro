/* =====================================================================
   STUDIO · STAGE 4 — YouTube package (title, description, tags) and thumbnails
   ===================================================================== */
function ytEditedText(maxChars){
  const out=[];let n=0;STU.words.forEach((w,i)=>{if(STU.removed[i]||w.snd||n>maxChars)return;out.push(w.w);n+=w.w.length+1});return out.join(' ');
}
function ytFullText(){
  const y=STU.yt||{};const parts=[];
  if(y.title)parts.push(y.title);
  if(y.description)parts.push(y.description);
  const ch=typeof chaptersText==='function'?chaptersText():'';if(ch)parts.push(ch);
  if(y.tags)parts.push('Tags: '+y.tags);
  return parts.join('\n\n');
}
async function ytWrite(){
  if(!STU.words.length){stuSay('#ytStatus','Transcribe first (step 5).');return}
  stuSay('#ytStatus','Asking AI for a title, description and tags…');
  try{
    const got=await askAny(`Below is the edited transcript of a recorded spiritual talk (Pranahuti Yoga / Sahaj Marg tradition, channel "Divine Light Meditation").
Write YouTube metadata that is respectful, clear and true to the talk (no clickbait, no invented claims, keep any quoted wording exact):
- "titles": 5 title options, each under 70 characters;
- "description": 2 short paragraphs (what the talk is about, who it is for), then one line inviting viewers to try meditation; no hashtags, no chapter list (it is added separately);
- "tags": 10 to 15 tags.
Reply with only JSON: {"titles":["..."],"description":"...","tags":["..."]}

TRANSCRIPT:
${ytEditedText(24000)}`,{json:true,onStatus:t=>stuSay('#ytStatus',t)});
    const d=parseJSON(got.text);
    STU.yt={titles:(d.titles||[]).slice(0,6).map(t=>String(t).slice(0,100)),title:String((d.titles||[])[0]||'').slice(0,100),description:String(d.description||''),tags:(d.tags||[]).map(String).join(', ')};
    stuSave();ytRender();stuSay('#ytStatus',`Written with ${PROVIDERS[got.pid].label}. Pick a title and edit anything you like; chapters are added to the description automatically.`);
  }catch(e){stuSay('#ytStatus',errMsg(e))}
}
function ytRender(){
  const y=STU.yt||{};
  $('#ytTitles').innerHTML=(y.titles||[]).map((t,i)=>`<label class="check"><input type="radio" name="ytTitle" value="${i}"${t===y.title?' checked':''}> ${esc(t)}</label>`).join('');
  $('#ytTitle').value=y.title||'';$('#ytDesc').value=y.description||'';$('#ytTags').value=y.tags||'';
  const ch=typeof chaptersText==='function'?chaptersText():'';$('#ytChapNote').textContent=ch?`+ ${ch.split('\n').length} chapters added at the end`:'(no chapters yet — step 7)';
  const n=(y.title||'').length;$('#ytTitleCount').textContent=n?`${n}/100 characters${n>70?' — long titles get cut off on phones':''}`:'';
}

/* ---------- thumbnails ---------- */
let TH={img:null,file:null};
async function thSuggest(){
  if(!engNeed())return;stuSay('#thStatus','The engine is choosing the best moments (see progress above)…');
  try{const r=await engRun('thumbs',{video:STU.engVideo,n:6,settings:picRenderSettings()});
    $('#thGrid').innerHTML=r.thumbs.map((t,i)=>`<button class="th-pick" data-th="${esc(t.file)}" title="${fmtT(t.t)}"><img src="${engFileUrl(t.file)}" alt="Moment at ${fmtT(t.t)}"></button>`).join('');
    stuSay('#thStatus','Click a picture to design the thumbnail.')}
  catch(e){stuSay('#thStatus',e.message)}
}
function thDraw(){
  const c=$('#thCanvas'),x=c.getContext('2d');x.clearRect(0,0,1280,720);
  if(TH.img)x.drawImage(TH.img,0,0,1280,720);else{x.fillStyle='#222';x.fillRect(0,0,1280,720)}
  const text=($('#thText').value||'').trim(),font=$('#thFont').value,pos=$('#thPos').value,col=$('#thColour').value;
  if(!text)return;
  const g=pos==='left'?x.createLinearGradient(0,0,760,0):x.createLinearGradient(0,720,0,330);
  g.addColorStop(0,'rgba(0,0,0,0.72)');g.addColorStop(1,'rgba(0,0,0,0)');x.fillStyle=g;x.fillRect(0,0,1280,720);
  const maxW=pos==='left'?620:1160;let size=120;const lines=t=>{const words=t.split(/\s+/),out=[];let cur='';for(const w of words){const tr=cur?cur+' '+w:w;if(x.measureText(tr).width>maxW&&cur){out.push(cur);cur=w}else cur=tr}if(cur)out.push(cur);return out};
  let ls;do{x.font=`700 ${size}px '${font}'`;ls=lines(text);size-=4}while((ls.length>3||ls.some(l=>x.measureText(l).width>maxW))&&size>40);
  const lh=size*1.12;x.fillStyle=col;x.strokeStyle='rgba(0,0,0,0.85)';x.lineWidth=Math.max(4,size/12);x.lineJoin='round';x.textBaseline='alphabetic';
  ls.forEach((l,i)=>{const y=pos==='left'?360-(ls.length*lh)/2+lh*(i+0.8):690-lh*(ls.length-1-i)-20,xx=pos==='left'?60:640;x.textAlign=pos==='left'?'left':'center';x.strokeText(l,xx,y);x.fillText(l,xx,y)});
}
function stage4Init(){
  $('#ytWrite').addEventListener('click',ytWrite);
  $('#ytTitles').addEventListener('change',e=>{const i=+e.target.value;STU.yt.title=STU.yt.titles[i];stuSave();setTimeout(ytRender,0)});
  [['#ytTitle','title'],['#ytDesc','description'],['#ytTags','tags']].forEach(([id,k])=>$(id).addEventListener('input',e=>{STU.yt={...(STU.yt||{}),[k]:e.target.value};stuSave();if(k==='title')ytRender()}));
  $('#ytCopy').addEventListener('click',()=>{const t=ytFullText();navigator.clipboard.writeText(t).then(()=>stuSay('#ytStatus','Copied — paste it into YouTube Studio.')).catch(()=>prompt('Copy:',t))});
  $('#thSuggestBtn').addEventListener('click',thSuggest);
  $('#thGrid').addEventListener('click',e=>{const b=e.target.closest('[data-th]');if(!b)return;const img=new Image();img.crossOrigin='anonymous';
    img.onload=()=>{TH.img=img;TH.file=b.dataset.th;if(!$('#thText').value)$('#thText').value=(STU.yt&&STU.yt.title)||'';$('#thDesign').hidden=false;$('#thFont').dispatchEvent(new Event('input'))};img.src=engFileUrl(b.dataset.th)});
  const redraw=()=>{const f=$('#thFont').value;(document.fonts&&document.fonts.load?document.fonts.load(`700 60px '${f}'`):Promise.resolve()).catch(()=>{}).then(thDraw)};
  ['thText','thFont','thPos','thColour'].forEach(id=>$('#'+id).addEventListener('input',redraw));
  $('#thSave').addEventListener('click',()=>$('#thCanvas').toBlob(b=>saveBlob(b,'YouTube thumbnail.jpg'),'image/jpeg',0.92));
  ytRender();
}
