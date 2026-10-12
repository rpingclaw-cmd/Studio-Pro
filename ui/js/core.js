"use strict";
/* =====================================================================
   Studio Pro — shared helpers (taken from the Daily Quote Cards website, Studio part):
   storage, small utilities, and the AI connections (Groq / Gemini / free backups) used for
   transcription, Reel and chapter suggestions and the YouTube text. Keys are kept on this computer.
   ===================================================================== */
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const uidGen=()=>Math.random().toString(36).slice(2,10);
const clone=o=>JSON.parse(JSON.stringify(o));
const todayStr=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const DEFAULT_MODEL='gemini-2.5-flash';
const KEY_STORE='sqc-gemini-key';
/* app settings (AI model choices) — saved on this computer */
let state={settings:{ai:{models:{}}}};
try{const s=JSON.parse(localStorage.getItem('studio-pro-settings')||'null');if(s&&s.ai)state.settings=s}catch(e){}
function scheduleSave(){try{localStorage.setItem('studio-pro-settings',JSON.stringify(state.settings))}catch(e){}}
const IDB={db:null,
  open(){if(this._opening)return this._opening;return this._opening=new Promise((res,rej)=>{try{const r=indexedDB.open('samadristi-studio',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('kv');r.onsuccess=()=>{this.db=r.result;res()};r.onerror=()=>rej(r.error)}catch(e){rej(e)}})},
  get(k){return new Promise(res=>{if(!this.db)return res(undefined);const q=this.db.transaction('kv').objectStore('kv').get(k);q.onsuccess=()=>res(q.result);q.onerror=()=>res(undefined)})},
  set(k,v){return new Promise((res,rej)=>{if(!this.db)return rej(new Error('no db'));const tx=this.db.transaction('kv','readwrite');tx.objectStore('kv').put(v,k);tx.oncomplete=()=>res();tx.onerror=()=>rej(tx.error)})},
  del(k){return new Promise(res=>{if(!this.db)return res();const tx=this.db.transaction('kv','readwrite');tx.objectStore('kv').delete(k);tx.oncomplete=()=>res();tx.onerror=()=>res()})},
  keys(){return new Promise(res=>{if(!this.db)return res([]);const q=this.db.transaction('kv').objectStore('kv').getAllKeys();q.onsuccess=()=>res(q.result||[]);q.onerror=()=>res([])})}
};
function saveBlob(blob,name){
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},5000);
}
const API='https://generativelanguage.googleapis.com';
function geminiKey(){try{return localStorage.getItem(KEY_STORE)||''}catch(e){return window.__key||''}}
function geminiModel(){return (state.settings.ai.model||DEFAULT_MODEL).replace(/^models\//,'').trim()}
function rankModels(names){
  const ok=names.filter(n=>/^gemini-/.test(n)&&/flash|pro/.test(n)&&!/tts|image|embed|live|audio|transcribe|computer|robotics|learnlm|exp-\d/.test(n));
  const ver=n=>{const m=n.match(/gemini-(\d+(?:\.\d+)?)/);return m?parseFloat(m[1]):0};
  const score=n=>{let s=ver(n)*10;if(/pro/.test(n))s-=200;if(/lite/.test(n))s-=100;if(/preview|exp/.test(n))s-=50;if(/latest/.test(n))s-=2;if(/-\d{3}$|-\d{2}-\d{4}$/.test(n))s-=5;return s};
  return [...new Set(ok)].sort((a,b)=>score(b)-score(a));
}
async function listModels(key){
  const r=await fetch(`${API}/v1beta/models?pageSize=1000`,{headers:{'x-goog-api-key':key}});const d=await r.json().catch(()=>({}));
  if(!r.ok) throw {code:r.status,message:(d.error&&d.error.message)||r.statusText};
  return (d.models||[]).filter(m=>(m.supportedGenerationMethods||[]).includes('generateContent')).map(m=>m.name.replace(/^models\//,''));
}
async function testModel(key,model){
  try{const r=await fetch(`${API}/v1beta/models/${encodeURIComponent(model)}:generateContent`,{method:'POST',headers:{'Content-Type':'application/json','x-goog-api-key':key},
    body:JSON.stringify({contents:[{role:'user',parts:[{text:'Reply with the word OK.'}]}],generationConfig:{maxOutputTokens:200}})});
    const d=await r.json().catch(()=>({}));return {ok:r.ok,status:r.status,message:(d.error&&d.error.message)||''};
  }catch(e){return {ok:false,status:0,message:String(e)}}
}
const isKeyError=t=>t.status===401||t.status===403||(t.status===400&&/api key/i.test(t.message||''));
async function autoPickModel(onStatus){
  const key=geminiKey(); if(!key) throw {code:'nokey'};
  const names=await listModels(key);
  const ranked=rankModels(names);
  let lastErr=null;
  for(const m of ranked.slice(0,10)){
    onStatus&&onStatus('Trying '+m+'…');
    const t=await testModel(key,m);
    if(t.ok){state.settings.ai.model=m;scheduleSave();return m}
    if(isKeyError(t)) throw {code:t.status,message:t.message};
    lastErr=t;
  }
  throw {code:'nomodel',message:lastErr?lastErr.message:'no models listed for this key'};
}
async function gemini(parts,{json=false,signal,maxTokens,onStatus}={}){
  const key=geminiKey(); if(!key) throw {code:'nokey'};
  const body={contents:[{role:'user',parts}],generationConfig:{temperature:0.2}};
  if(json) body.generationConfig.responseMimeType='application/json';
  if(maxTokens) body.generationConfig.maxOutputTokens=maxTokens;
  let switched=false, busyTries=0, rateTries=0;
  for(;;){
    const r=await fetch(`${API}/v1beta/models/${encodeURIComponent(geminiModel())}:generateContent`,{method:'POST',signal,headers:{'Content-Type':'application/json','x-goog-api-key':key},body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(r.ok){
      const c=d.candidates&&d.candidates[0];
      const text=((c&&c.content&&c.content.parts)||[]).filter(p=>!p.thought).map(p=>p.text||'').join('');
      if(!text) throw {code:'empty',message:(d.promptFeedback&&d.promptFeedback.blockReason)||(c&&c.finishReason)||'no answer'};
      return text;
    }
    const msg=(d.error&&d.error.message)||r.statusText;
    if(!switched&&(r.status===404||(r.status===400&&/not (found|supported)|no longer available|unsupported model/i.test(msg)))){
      switched=true;onStatus&&onStatus('That model is not available on your key — finding one that works…');
      await autoPickModel(onStatus);continue;
    }
    if((r.status===500||r.status===503)&&busyTries<3){busyTries++;onStatus&&onStatus('Gemini is busy — retrying…');await sleep(4000*busyTries);continue}
    if(r.status===429&&rateTries<2){
      const info=((d.error&&d.error.details)||[]).find(x=>x.retryDelay);const wait=info?parseFloat(info.retryDelay):30;
      if(wait<=65&&!/per day|PerDay/i.test(msg)){rateTries++;onStatus&&onStatus(`Free-tier speed limit — waiting ${Math.ceil(wait)} seconds…`);await sleep((wait+1)*1000);continue}
    }
    throw {code:r.status,message:msg};
  }
}
function parseJSON(t){try{return JSON.parse(t)}catch(e){const m=String(t).match(/[\[{][\s\S]*[\]}]/);if(m)return JSON.parse(m[0]);throw e}}
function errMsg(e){
  if(!e) return 'Something went wrong.';
  if(e.name==='AbortError') return 'Stopped.';
  if(e.code==='allfailed') return e.message;
  if(e.code==='nokey') return 'Add your free Gemini key in Setup first.';
  if(e.code===400) return 'Gemini could not process this: '+(e.message||'bad request')+'. Try a smaller page range or file.';
  if(e.code===401||e.code===403) return 'The Gemini key was not accepted. Check it in Setup.';
  if(e.code==='nomodel') return 'No Gemini model on this key accepted a request ('+(e.message||'')+'). Try again later or create a new key in AI Studio.';
  if(e.code===404) return 'Gemini model not available: '+(e.message||'')+' Press Check key in Setup.';
  if(e.code===429) return /per day|PerDay|daily|quota|exhausted/i.test(e.message||'')?'daily free limit used up':'speed limit reached';
  if(e.code==='upload') return e.message;
  if(e.code==='empty') return 'Gemini returned no text ('+e.message+'). Try again or use a smaller piece.';
  return 'Something went wrong: '+(e.message||e.code||e);
}
function blobToBase64(blob){return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(String(r.result).split(',')[1]);r.onerror=rej;r.readAsDataURL(blob)})}
/* ---------- multi-provider AI (Gemini + free backups) ---------- */
const PROVIDERS={
  gemini:{label:'Google Gemini',maxChars:110000,gap:5000,vision:true},
  mistral:{label:'Mistral',url:'https://api.mistral.ai/v1',model:'mistral-small-latest',maxChars:70000,gap:1500,vision:true,keyUrl:'console.mistral.ai',pick:[/^mistral-small-latest$/,/^mistral-medium-latest$/,/^mistral-small/,/^mistral-large-latest$/]},
  cerebras:{label:'Cerebras',url:'https://api.cerebras.ai/v1',model:'',maxChars:16000,gap:4000,keyUrl:'cloud.cerebras.ai',pick:[/gpt-oss-120b/,/llama-3\.3-70b/,/qwen-3-235b/,/llama/,/qwen/]},
  groq:{label:'Groq',url:'https://api.groq.com/openai/v1',model:'llama-3.3-70b-versatile',maxChars:11000,gap:12000,keyUrl:'console.groq.com',pick:[/^llama-3\.3-70b-versatile$/,/gpt-oss-120b/,/llama-4-scout/,/llama/]},
  openrouter:{label:'OpenRouter',url:'https://openrouter.ai/api/v1',model:'',maxChars:40000,gap:5000,keyUrl:'openrouter.ai/keys',pick:[/llama-3\.3-70b.*:free$/,/gpt-oss-120b:free$/,/deepseek.*:free$/,/:free$/]}
};
const BACKUPS=['mistral','cerebras','groq','openrouter'];
const cooled={}; // pid -> time until which it is skipped
function pKey(pid){if(pid==='gemini')return geminiKey();try{return localStorage.getItem('sqc-key-'+pid)||''}catch(e){return ''}}
function pModel(pid){if(pid==='gemini')return geminiModel();const m=(state.settings.ai.models||{})[pid];return m||PROVIDERS[pid].model}
function availableProviders(needVision){return ['gemini',...BACKUPS].filter(p=>pKey(p)&&(!needVision||PROVIDERS[p].vision))}
function nextProvider(needVision){const now=Date.now();return availableProviders(needVision).find(p=>!(cooled[p]>now))||null}
async function oaiChat(pid,prompt,{json,signal,images}){
  const P=PROVIDERS[pid];let model=pModel(pid);
  if(!model){model=await autoModel(pid);if(!model)throw {code:404,message:'no model found'}}
  const content=images&&images.length?[{type:'text',text:prompt},...images.map(u=>({type:'image_url',image_url:{url:u}}))]:prompt;
  const body={model,messages:[{role:'user',content}],temperature:0.2};
  if(json&&pid==='mistral') body.response_format={type:'json_object'};
  const headers={'Content-Type':'application/json','Authorization':'Bearer '+pKey(pid)};
  if(pid==='openrouter'){headers['HTTP-Referer']=location.origin;headers['X-Title']='Daily Quote Cards'}
  const r=await fetch(P.url+'/chat/completions',{method:'POST',signal,headers,body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){const msg=(d.error&&(d.error.message||d.error))||d.message||r.statusText;throw {code:r.status,message:typeof msg==='string'?msg:JSON.stringify(msg),retryAfter:+(r.headers.get('retry-after')||0)}}
  const text=d.choices&&d.choices[0]&&d.choices[0].message&&d.choices[0].message.content;
  if(!text) throw {code:'empty',message:'no answer'};
  return Array.isArray(text)?text.map(x=>x.text||'').join(''):text;
}
async function listOaiModels(pid){
  const r=await fetch(PROVIDERS[pid].url+'/models',{headers:{'Authorization':'Bearer '+pKey(pid)}});const d=await r.json().catch(()=>({}));
  if(!r.ok) throw {code:r.status,message:(d.error&&(d.error.message||d.error))||d.message||r.statusText};
  return (d.data||[]).map(m=>m.id);
}
async function autoModel(pid){
  const ids=await listOaiModels(pid);const P=PROVIDERS[pid];let m='';
  for(const re of P.pick){m=ids.find(i=>re.test(i));if(m)break}
  m=m||ids[0]||'';if(m){state.settings.ai.models=state.settings.ai.models||{};state.settings.ai.models[pid]=m;scheduleSave()}
  return m;
}
/* One request to one provider, with a short wait-and-retry on speed limits. */
async function callProvider(pid,prompt,opts){
  const ctl=new AbortController();const outer=opts.signal;const onAbort=()=>ctl.abort();if(outer){if(outer.aborted)ctl.abort();else outer.addEventListener('abort',onAbort)}
  const timer=setTimeout(()=>ctl.abort(),150000);
  try{return await callProviderInner(pid,prompt,{...opts,signal:ctl.signal})}
  catch(e){if(e&&e.name==='AbortError'&&!(outer&&outer.aborted))throw {code:'timeout',message:'the AI took too long to answer'};throw e}
  finally{clearTimeout(timer);if(outer)outer.removeEventListener('abort',onAbort)}
}
async function callProviderInner(pid,prompt,{json,signal,images,parts,onStatus}){
  const maxTry=pid==='mistral'?5:2;
  for(let attempt=0;attempt<maxTry;attempt++){
    try{
      if(pid==='gemini'){
        const gp=[...(parts||[]),...(images||[]).map(u=>({inline_data:{mime_type:u.slice(5,u.indexOf(';')),data:u.split(',')[1]}})),{text:prompt}];
        return await gemini(gp,{json,signal,onStatus});
      }
      return await oaiChat(pid,prompt,{json,signal,images});
    }catch(e){
      if(e&&e.name==='AbortError') throw e;
      const daily=/per day|PerDay|daily|quota/i.test((e&&e.message)||'');
      if(e&&e.code===400&&/validate JSON|failed_generation|json/i.test(e.message||'')&&attempt===0){json=false;continue}
      if(e&&e.code===429&&!daily&&attempt<maxTry-1){const w=Math.min(65,e.retryAfter||(pid==='mistral'?[4,8,15,25][attempt]:30));onStatus&&onStatus(`${PROVIDERS[pid].label} speed limit — waiting ${w}s…`);await sleep(w*1000);continue}
      throw e;
    }
  }
}
function coolDown(pid,e){
  const daily=/per day|PerDay|daily|quota|exhausted/i.test((e&&e.message)||'');
  const temporary=e&&([500,502,503,504,'timeout','empty',0].includes(e.code)||e.name==='TypeError');
  cooled[pid]=Date.now()+(e&&(e.code===401||e.code===403)?864e5:daily?6*3600e3:temporary?20e3:120e3);
}
function providerError(pid,e){return `${PROVIDERS[pid].label}: ${errMsg(e).replace(/^Gemini /,'')}`}
/* Ask the next working provider; on limits, move on to the next one. */
async function askAny(prompt,{json,signal,images,parts,onStatus,needVision,maxLen}){
  const tried=[];
  for(;;){
    const pid=nextProvider(needVision);
    if(!pid){const e=new Error(tried.length?tried.join(' · '):'Add a free AI key in Setup first.');e.code='allfailed';throw e}
    if(maxLen&&maxLen>PROVIDERS[pid].maxChars) return {resize:pid};
    onStatus&&onStatus(`Asking ${PROVIDERS[pid].label}…`);
    try{const text=await callProvider(pid,prompt,{json,signal,images,parts,onStatus});return {text,pid}}
    catch(e){if(e&&e.name==='AbortError')throw e;tried.push(providerError(pid,e));coolDown(pid,e)}
  }
}

