/* =====================================================================
   Studio Pro — start-up, Preferences, and the desktop-app hooks.
   The page is served by the app's own Studio Engine; the editor opens straight away.
   ===================================================================== */
const PREF_KEYS=['groq','gemini','mistral','cerebras','openrouter'];
function prefStore(id){return id==='gemini'?KEY_STORE:'sqc-key-'+id}
function prefsOpen(){
  PREF_KEYS.forEach(id=>{const el=$('#prefKey_'+id);try{el.value=localStorage.getItem(prefStore(id))||''}catch(e){el.value=''}});
  $('#appPrefs').showModal();
}
function prefsSave(){PREF_KEYS.forEach(id=>{const v=$('#prefKey_'+id).value.trim();try{if(v)localStorage.setItem(prefStore(id),v);else localStorage.removeItem(prefStore(id))}catch(e){}})}
async function appStart(){
  await stuLoad();wireStudio();stuRenderInfo();stuRenderTranscript();engInit();stage2Init();picInit();stage4Init();edInit();
  $('#appPrefsBtn').addEventListener('click',prefsOpen);
  $('#appEditorBtn').addEventListener('click',edOpen);
  $('#appPrefs').addEventListener('close',()=>{if($('#appPrefs').returnValue==='save')prefsSave()});
  const v=window.studioApp&&window.studioApp.version;$('#appAbout').textContent=`Studio Pro ${v||''} — talks and podcasts: sync, clean sound, edit by text, Reels, YouTube.`;
  // drop a video or audio file anywhere: it becomes the video (or the separate audio)
  document.addEventListener('dragover',e=>{if(e.dataTransfer&&[...e.dataTransfer.types].includes('Files'))e.preventDefault()});
  document.addEventListener('drop',e=>{const f=e.dataTransfer&&e.dataTransfer.files[0];if(!f||!window.studioApp||!window.studioApp.pathOf)return;e.preventDefault();
    const path=window.studioApp.pathOf(f);if(!path)return;const audio=/\.(wav|mp3|m4a|aac|flac|ogg|opus)$/i.test(path);
    if(audio){STU.settings.separate=true;$('#stu_separate').checked=true;stuSave();stuRenderInfo()}
    engSetFile(audio?'audio':'video',path)});
  edOpen();  // the editor is the main window; "Guided view" shows the step-by-step page
}
appStart();
