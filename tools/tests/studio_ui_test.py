"""Browser test of the whole Studio Pro app page (served by its own Studio Engine, as in the desktop app), stages 1-4,
with screenshots.

Needs: Playwright for Python, Chromium, ffmpeg, espeak-ng, and test media made by engine/tests/media_gen.py:
  cd engine && uv run python -c "import json,sys;sys.path.insert(0,'tests');import media_gen;from pathlib import Path;\
    m=media_gen.make(Path('/tmp/uimedia'),video_s=90.0,size='540x960',face='/path/to/face.jpg');\
    Path('/tmp/uimedia/media.json').write_text(json.dumps(m))"
Run:  STUDIO_UI_MEDIA=/tmp/uimedia/media.json STUDIO_DEEP_FILTER=/path/deep-filter python tools/tests/studio_ui_test.py /tmp/shots
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/studio_ui_shots")
OUT.mkdir(parents=True, exist_ok=True)
MEDIA = json.loads(Path(os.environ.get("STUDIO_UI_MEDIA", "/tmp/uimedia/media.json")).read_text())
HOME = Path(os.environ.get("STUDIO_UI_HOME", "/tmp/studio_ui_home"))
env = {**os.environ, "STUDIO_ENGINE_HOME": str(HOME)}
CHROME = os.environ.get("CHROMIUM_PATH")  # optional: a specific Chromium build

TOKEN = "ui-test-token"
env["STUDIO_ENGINE_TOKEN"] = TOKEN
APP = f"http://127.0.0.1:8765/#engine={TOKEN}"
def start_engine():
    e = subprocess.Popen(["uv", "run", "--project", str(ROOT / "engine"), "studio-engine", "--app"], env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/api/hello", timeout=1); break
        except Exception:
            time.sleep(0.3)
    return e
eng = None
log = []
def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
    log.append(name)

def wait_text(page, sel, text, timeout=600):
    t0 = time.time()
    while text not in (page.locator(sel).inner_text() or ""):
        assert time.time() - t0 < timeout, f"timeout waiting for {text!r} in {sel}: {page.locator(sel).inner_text()!r}"
        time.sleep(0.5)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
        page = b.new_page(viewport={"width": 1100, "height": 1000}, bypass_csp=True)  # (test checks run as text; the app keeps its CSP)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (print("DIALOG:", d.message), d.dismiss()))
        time.sleep(1)
        # 1-2. the app page, served by its engine (the desktop app opens exactly this); the editor opens first
        eng = start_engine()
        page.goto(APP)
        wait_text(page, "#engStatus", "Engine connected", 60)
        wait_text(page, "#engStatus", "for video", 90)
        page.wait_for_selector("#ed:not([hidden])")
        shot(page, "01-app-start-editor")
        page.click("#edClose")  # the guided steps
        assert "engine=" not in page.url, "token must be removed from the address bar"
        page.locator("#engBox").scroll_into_view_if_needed()
        page.click("#engOn summary")
        shot(page, "02-engine-connected")
        # 3. files via the paste-path fallback (the native window can't show in a headless test)
        if not page.locator("#stu_separate").is_checked():
            page.click("#stu_separate")
        page.click("#engFiles summary")
        page.fill("#engPastePath", MEDIA["video"]); page.click("button[data-paste=video]")
        wait_text(page, "#engVideoPath", "Talk video.mov", 10)
        page.fill("#engPastePath", MEDIA["audio"]); page.click("button[data-paste=audio]")
        wait_text(page, "#engAudioPath", "Recorder audio.m4a", 10)
        page.click("#engReportBtn")
        wait_text(page, "#engReport", "Free space", 300)
        page.locator("#engFiles").scroll_into_view_if_needed()
        shot(page, "03-file-report")
        # 4. prepare -> editor gets preview + sound automatically
        page.click("#engPrepBtn")
        page.wait_for_function("STU.engPrep && STU.wavV && STU.wavA && STU.info", timeout=600000)
        src = page.evaluate("document.querySelector('#stuVideo').src")
        assert src.startswith("http://127.0.0.1:8765/api/file")
        # (test Chromium has no H.264 decoder; real Chrome/Edge play it) - check the file is served with ranges
        st = page.evaluate("fetch(document.querySelector('#stuVideo').src,{headers:{Range:'bytes=0-99'}}).then(r=>r.status+' '+r.headers.get('content-type'))")
        assert st == "206 video/mp4", st
        assert abs(page.evaluate("STU.dur") - MEDIA["video_s"]) < 1
        shot(page, "04-prepared")
        # 5. sync
        page.locator("#stuSync").scroll_into_view_if_needed()
        page.click("#stuSync")
        wait_text(page, "#stuSyncOut", "Sync found", 300)
        sync = page.evaluate("STU.sync")
        A, B = MEDIA["A"], MEDIA["B"]
        for tv in (5, MEDIA["video_s"] / 2, MEDIA["video_s"] - 10):
            assert abs((sync["a"] + sync["b"] * tv) - (A + B * tv)) < 0.005, sync
        assert "Words agree at 3 of 3 points" in page.locator("#stuSyncOut").inner_text(), page.locator("#stuSyncOut").inner_text()
        # keep the whole recording: the recorder started ~3.2 s before the camera
        page.locator("#stuKeepAllBox").scroll_into_view_if_needed()
        assert "before the camera" in page.locator("#stuSpanInfo").inner_text(), page.locator("#stuSpanInfo").inner_text()
        assert page.evaluate("keepSegments()[0][0]") < -3.0
        shot(page, "05-synced")
        # 6. sound preview
        page.locator("#stuSound").scroll_into_view_if_needed()
        page.fill("#snd_strength", "80")
        page.dispatch_event("#snd_strength", "input")
        assert page.locator("#snd_strengthVal").inner_text() == "80"
        page.evaluate("document.querySelector('#stuVideo').currentTime=20")
        page.click("#engPreviewBtn")
        wait_text(page, "#engSoundStatus", "Preview from", 300)
        page.wait_for_function("document.querySelector('#engAfter').readyState>=1", timeout=30000)
        ad = page.evaluate("document.querySelector('#engAfter').duration")
        assert 29.5 < ad < 30.5, ad
        shot(page, "06-sound-preview")
        page.click("#engCleanBtn")  # clean the whole recording (the render re-uses it)
        wait_text(page, "#engSoundStatus", "Sound cleaned", 900)
        assert page.evaluate("!!STU.engClean")
        # 6b. STAGE 2: listen (voice map), a transcript, line up the owner's exact text, chapters
        page.locator("#stuAnalyse").scroll_into_view_if_needed()
        page.click("#stuAnalyse")
        wait_text(page, "#stuAnaStatus", "Voice found", 600)
        shot(page, "06b-voice-map")
        # real transcription on this computer (fast model), then a stand-in with known mistakes for the checks below
        page.select_option("#stu_asr", "fast"); page.select_option("#stu_lang", "en")
        page.click("#stuTranscribe")
        wait_text(page, "#stuTxStatus", "words", 900)
        assert page.evaluate("STU.words.length") > 80, page.locator("#stuTxStatus").inner_text()
        assert page.evaluate("STU.words.some(w=>w.s<0)"), "words from before the camera started are kept"
        shot(page, "06a-transcribed-locally")
        # stand-in for the transcription: words spread over the detected voice,
        # with a few mis-heard words and some "um"s the speaker said
        page.evaluate("""(script)=>{const segs=ANA.speech;const words=[];let k=0;
          script.slice(0,segs.length).forEach((sent,si)=>{const seg=segs[si];const toks=sent.split(' ');const d=(seg[1]-seg[0])/toks.length;
            toks.forEach((w,i)=>{let h=w.replace(/[.]/g,'');if(si%7===3&&i===2)h='banana';words.push({w:h,s:seg[0]+i*d,e:seg[0]+(i+0.85)*d})});
            if(si%5===1)words.push({w:'um',s:seg[1]+0.05,e:seg[1]+0.25})});
          STU.words=words.sort((a,b)=>a.s-b.s);STU.asrWords=STU.words.map(w=>({...w}));STU.removed={};autoMarks();stuRenderTranscript()}""", MEDIA["script"])
        nseg = page.evaluate("ANA.speech.length")
        page.locator("#stuOwnText").fill(" ".join(MEDIA["script"][:nseg]))
        page.click("#stuAlign")
        wait_text(page, "#stuAlignStatus", "of your words matched", 30)
        align = page.locator("#stuAlignStatus").inner_text()
        pct = float(align.split("Lined up: ")[1].split("%")[0])
        assert pct >= 95, align
        n_um = page.evaluate("STU.asrWords.filter(w=>w.w==='um').length")
        assert n_um >= 2 and page.evaluate("STU.words.filter(w=>w.x&&w.w==='um').length") == n_um, "every um found"
        assert page.evaluate("STU.words.filter((w,i)=>w.x&&w.w==='um'&&STU.removed[i]==='auto').length") == n_um, "ums cut automatically"
        page.locator("#stuText").scroll_into_view_if_needed()
        shot(page, "06c-transcript-lined-up")
        # cut one whole sentence by hand, add chapters
        page.evaluate("for(let i=20;i<30;i++)STU.removed[i]='user';stuSave();stuRenderTranscript()")
        page.click("#stuChapAdd")  # (test browser can't play H.264, so set the times directly)
        page.evaluate("STU.chapters=[{t:0,title:'Opening'},{t:40,title:'New chapter'},{t:70,title:'New chapter'}];stuRenderChapters()")
        page.locator("#stuChapters input").nth(1).fill("The heart"); page.locator("#stuChapters input").nth(1).dispatch_event("input")
        page.locator("#stuChapters").scroll_into_view_if_needed()
        shot(page, "06d-chapters")
        chap = page.evaluate("chaptersText()")
        assert chap.startswith("0:00 ") and "The heart" in chap, chap
        times = [l.split(" ")[0] for l in chap.splitlines()]
        assert len(set(times)) == 3 and all(t != "0:00" for t in times[1:]), chap  # edited times, all different
        stats = page.locator("#stuStats").inner_text()
        assert "words removed" in stats, stats
        # 6e. STAGE 3: find the speaker, framing + colour still, a cover, a reel with word-by-word captions
        page.locator("#stuPicture").scroll_into_view_if_needed()
        page.click("#picFaces")
        wait_text(page, "#picFacesStatus", "Speaker found", 600)
        page.select_option("#pic_layout", "crop169"); page.select_option("#pic_punch", "1.18")
        page.fill("#pic_warmth", "0.3"); page.dispatch_event("#pic_warmth", "input")
        page.click("#picPreview")
        wait_text(page, "#picStatus", "1920×1080", 300)
        page.wait_for_function("document.querySelector('#picStill').complete && document.querySelector('#picStill').naturalWidth===1920", timeout=30000)
        page.click("#picPreviewClose")
        wait_text(page, "#picStatus", "close-up", 300)
        page.wait_for_function("document.querySelector('#picStill').complete && document.querySelector('#picStill').naturalWidth===1920", timeout=30000)
        page.click("#picCoverAdd")
        page.locator("#picCovers select").select_option("title")
        page.locator("#picCovers input[data-k=title]").fill("Divine Light"); page.locator("#picCovers input[data-k=title]").dispatch_event("change")
        page.locator("#picCovers input[data-k=start]").fill("0:00"); page.locator("#picCovers input[data-k=start]").dispatch_event("change")
        page.locator("#picCovers input[data-k=end]").fill("0:08"); page.locator("#picCovers input[data-k=end]").dispatch_event("change")
        assert page.evaluate("JSON.stringify(picCovers())") == '[{"start":0,"end":8,"mode":"title","file":null,"title":"Divine Light","from":null}]', page.evaluate("JSON.stringify(picCovers())")
        page.evaluate("STU.reels=[{title:'The heart',start:40,end:52}];stuSave();stuStats()")
        page.locator("#stuPicture").scroll_into_view_if_needed()
        shot(page, "06e-picture")
        # background: plain colour behind the speaker (person found once per video), checked on a still
        page.select_option("#pic_bgMode", "colour")
        page.fill("#pic_bgColour", "#c81e1e"); page.dispatch_event("#pic_bgColour", "change")
        page.click("#picMatte")
        t0 = time.time()  # (this test video is a small face photo on grey, so "hardly any person" is also right here)
        while not any(w in page.locator("#picMatteStatus").inner_text() for w in ("Person found", "Hardly any person")):
            assert time.time() - t0 < 900, page.locator("#picMatteStatus").inner_text()
            time.sleep(1)
        page.evaluate("document.querySelector('#stuVideo').currentTime=30")
        page.click("#picPreview")
        wait_text(page, "#picStatus", "Still at", 300)
        page.wait_for_function("document.querySelector('#picStill').complete && document.querySelector('#picStill').naturalWidth>0", timeout=30000)
        page.locator("#picStill").scroll_into_view_if_needed()
        shot(page, "06e2-background-colour")
        # own framing box: drawn with the mouse in the editor (whole video), then back to automatic
        page.click("#picRectDraw")
        page.wait_for_selector("#edFrameBox:not([hidden])")
        bx = page.locator("#edFrameBox .ed-fb-box").bounding_box()
        page.mouse.move(bx["x"] + bx["width"] / 2, bx["y"] + bx["height"] / 2); page.mouse.down()
        page.mouse.move(bx["x"] + bx["width"] / 2, bx["y"] + bx["height"] / 2 - 300, steps=5); page.mouse.up()
        shot(page, "06e3-framing-box")
        page.click("#edFbDone")
        r = page.evaluate("pic().rect")
        assert r and r["y"] < 0.02, r  # moved to the top
        assert page.evaluate("JSON.stringify(picRenderSettings().rect)") == page.evaluate("JSON.stringify(pic().rect)")
        page.click("#edClose")
        page.click("#picRectAuto"); assert page.evaluate("pic().rect") is None
        # 6f. STAGE 4: YouTube text (AI is not reachable here: fill it in), thumbnails from the engine
        page.evaluate("STU.yt={titles:['The heart and the divine light','Meditation on the heart'],title:'The heart and the divine light',description:'A talk about meditation on the heart.',tags:'meditation, heart, Pranahuti'};stuSave();ytRender()")
        full = page.evaluate("ytFullText()")
        assert full.startswith("The heart and the divine light") and "0:00 " in full and "Tags: meditation" in full, full
        page.locator("#stuYouTube").scroll_into_view_if_needed()
        page.click("#thSuggestBtn")
        wait_text(page, "#thStatus", "Click a picture", 600)
        assert page.locator("#thGrid img").count() >= 3
        page.locator("#thGrid .th-pick").first.click()
        page.wait_for_selector("#thDesign:not([hidden])", timeout=20000)
        page.fill("#thText", "The Heart"); page.dispatch_event("#thText", "input")
        page.select_option("#thFont", "Cinzel"); page.select_option("#thColour", "#F2C14E")
        time.sleep(1.5)
        with page.expect_download() as dl:
            page.click("#thSave")
        thumb = OUT / "thumbnail.jpg"; dl.value.save_as(str(thumb))
        import subprocess as sp
        td = sp.run(["ffprobe","-v","error","-show_entries","stream=width,height","-of","csv=p=0",str(thumb)],capture_output=True,text=True).stdout.strip()
        assert td == "1280,720", td
        page.locator("#stuYouTube").scroll_into_view_if_needed()
        shot(page, "06f-youtube-thumbnail")
        # 6g. the DaVinci Resolve-style editor: blade two cuts, ripple delete the piece, undo/redo, pages, keys
        page.click("#appEditorBtn")
        page.wait_for_selector("#ed:not([hidden])")
        assert page.evaluate("document.querySelector('#edScreen #stuVideo')!==null")
        time.sleep(0.5)
        # the editor plays the cleaned recorder sound with the picture (camera sound muted)
        assert page.evaluate("ED.recorder===true && stuVideo().muted===true"), page.evaluate("[ED.recorder, STU.engClean]")
        st = page.evaluate("fetch(document.querySelector('#edAudio').src,{headers:{Range:'bytes=0-99'}}).then(r=>r.status+' '+r.headers.get('content-type'))")
        assert st == "206 audio/wav", st
        page.click("#edSndBtn"); assert page.evaluate("ED.recorder===false && stuVideo().muted===false")
        page.click("#edSndBtn"); assert page.evaluate("ED.recorder===true")
        # the start of the timeline is before the camera: the picture is filled, the cleaned sound plays
        # (the stand-in transcript starts at the camera's 0:00, so 'trim silence at the start' would trim it)
        page.evaluate("STU.settings.trimEnds=false;stuSave();stuRenderTranscript()")
        assert page.evaluate("edToSrc(1.0)") < 0, page.evaluate("[keepSegments()[0], edToSrc(1.0)]")
        page.evaluate("document.activeElement.blur();edSeek(1.0)")
        assert page.evaluate("ED.virt!==null && ED.virt<0") and page.locator("#edNoCam").is_visible()
        shot(page, "06g2-editor-no-camera")
        page.keyboard.press(" ")
        page.wait_for_function("ED.virtPlay===true && !document.querySelector('#edAudio').paused", timeout=10000)
        v0 = page.evaluate("ED.virt"); time.sleep(1.2); v1 = page.evaluate("ED.virt")
        assert v1 > v0 + 0.5, (v0, v1)
        page.keyboard.press("k")
        page.evaluate("edSeek(20)")
        shot(page, "06g-editor-edit")
        tot0 = page.evaluate("edModel().total")
        page.evaluate("document.activeElement.blur();edSeek(edToEd(62))"); page.keyboard.press("Control+b")
        page.evaluate("edSeek(edToEd(66))"); page.keyboard.press("Control+b")
        cx = page.evaluate("(()=>{const r=document.querySelector('#edCanvas').getBoundingClientRect();return r.left+edX(edToEd(64))})()")
        # the middle of the V1 (video) track, wherever it is
        cy = page.evaluate("(()=>{const T=edTracks();let y=ED_RULER;for(const t of T){if(t.id==='V1')return document.querySelector('#edCanvas').getBoundingClientRect().top+y+t.h/2;y+=t.h}})()")
        page.mouse.click(cx, cy)
        assert page.evaluate("ED.sel&&ED.sel.type") == "clip", page.evaluate("ED.sel")
        page.keyboard.press("Delete")
        cut = tot0 - page.evaluate("edModel().total")
        assert 3.5 < cut < 4.5, cut  # (word-level snapping can move the edges a little)
        has64 = "keepSegments().some(([s,e])=>s<64&&e>64)"
        assert not page.evaluate(has64)
        page.keyboard.press("Control+z"); assert page.evaluate(has64), "undo brings it back"
        page.keyboard.press("Control+Shift+z"); assert not page.evaluate(has64), "redo removes it again"
        nch = page.evaluate("STU.chapters.length"); page.keyboard.press("m"); assert page.evaluate("STU.chapters.length") == nch + 1
        page.keyboard.press("Control+z"); assert page.evaluate("STU.chapters.length") == nch
        shot(page, "06h-editor-cut")
        for pg, where in (("media", "#edLeft #stuStep1"), ("color", "#edRight #stuPicture"), ("fairlight", "#edLeft #stuSound"), ("deliver", "#edLeft #stuStep10")):
            page.click(f"#ed [data-edpage={pg}]")
            assert page.evaluate(f"document.querySelector('{where}')!==null"), where
            time.sleep(0.4); shot(page, f"06i-editor-{pg}")
        page.click("#edClose")
        order = page.evaluate("[...document.querySelectorAll('#pane-studio > fieldset')].map(f=>f.id).join(',')")
        assert order == "engBox,stuStep1,stuStep2,stuStep3,stuSound,stuStep5,stuStep6,stuStep7,stuPicture,stuYouTube,stuStep10", order
        assert page.evaluate("document.querySelector('#stuStep6 #stuVideo')!==null && document.querySelector('#stuVideo').controls")
        edited_s, nparts = page.evaluate("[edModel().total, keepSegments().length]")
        # 6j. pro tools: a title (T key), background music (dips under the voice), smooth dissolves at cuts,
        #     a look + colour wheel, slow zoom and a quieter voice on one clip, extra copies
        page.click("#appEditorBtn"); page.wait_for_selector("#ed:not([hidden])")
        page.click("#ed [data-edpage=edit]")
        page.evaluate("document.activeElement.blur();edSeek(2.0)")
        page.keyboard.press("t")
        assert page.evaluate("ED.sel&&ED.sel.type") == "title"
        page.fill("#edInsp input[data-in=text]", "Divine Light Meditation"); page.dispatch_event("#edInsp input[data-in=text]", "change")
        page.fill("#edInsp input[data-in=sub]", "Talk on the heart"); page.dispatch_event("#edInsp input[data-in=sub]", "change")
        assert "Divine Light Meditation" in page.evaluate("titlesAss()") and "Lower" in page.evaluate("titlesAss()")
        page.evaluate("ED.dirty=true;edRefresh()")
        shot(page, "06j-title")
        music = Path(MEDIA["video"]).parent / "music.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=110:d=15", "-ar", "48000", str(music)], check=True)
        page.evaluate("p=>{STU.music={...edMusic(),file:p,db:-20};stuSave();ED.dirty=true;edRefresh()}", str(music))
        page.click("#ed [data-edpage=fairlight]"); time.sleep(0.4)
        assert "music.wav" in page.locator("#edMeterInfo").inner_text()
        shot(page, "06k-fairlight-music")
        page.click("#ed [data-edpage=color]"); time.sleep(0.3)
        page.select_option("#edLook", "warm")
        assert page.evaluate("pic().colour.gain.r") > 0 and page.evaluate("pic().colour.warmth") > 0
        wb = page.locator("#edWheels [data-wheel=lift] canvas").bounding_box()
        page.mouse.click(wb["x"] + wb["width"] * 0.3, wb["y"] + wb["height"] * 0.5)  # lift towards cyan-ish
        assert abs(page.evaluate("pic().colour.lift.r||0")) > 0.01
        shot(page, "06l-color-wheels")
        page.click("#edClose")
        page.select_option("#pic_cutStyle", "dissolve")
        assert page.evaluate("picRenderSettings().cut_style") == "dissolve" and page.evaluate("picRenderSettings().punch") == 1
        page.evaluate("const c=edModel().clips[1];STU.frames=[...(STU.frames||[]),{start:c.s,end:c.e,zoom:'auto',hold:false,kb:0.1}];STU.gains=[{start:c.s,end:c.e,db:-6}];stuSave()")
        assert any(o.get("kb") for o in page.evaluate("picOverrides()"))
        page.check("#stu_exWhatsapp"); page.check("#stu_exSquare")
        rp = page.evaluate("engRenderParams()")
        assert rp["music"]["file"].endswith("music.wav") and rp["titles_ass"] and rp["voice_gains"] and rp["settings"]["extras"] == ["whatsapp", "square"]
        # 7. render (edited by text, with cross-fades) + quality check
        page.locator("#engRenderBox").scroll_into_view_if_needed()
        page.select_option("#stu_res", "1080")
        page.evaluate("STU.settings.reelRes='720';$('#stu_reelRes').value='720'")
        page.click("#engRenderBtn")
        time.sleep(4)
        page.locator("#engBox").scroll_into_view_if_needed()
        shot(page, "07-render-progress")
        t0 = time.time()
        while page.locator("#engQC").is_hidden():
            assert time.time() - t0 < 1500, "render timeout"
            time.sleep(1)
        page.locator("#engRenderBox").scroll_into_view_if_needed()
        qc = page.locator("#engQC").inner_text()
        shot(page, "08-render-done-qc")
        bad = [l for l in qc.splitlines() if l.startswith("⚠") and not ("Captions:" in l and "fast to read" in l)]
        assert "Lip sync" in qc and not bad, qc  # (the synthetic voice is very fast: reading-speed notes are expected)
        assert "✓  Picture: no black or frozen stretches" in qc
        out_dir = Path(MEDIA["video"]).parent
        import subprocess as sp
        dims = sp.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0",str(out_dir/"Edited - full video.mp4")],capture_output=True,text=True).stdout.strip()
        assert dims == "1920,1080", dims
        fd = float(sp.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(out_dir/"Edited - full video.mp4")],capture_output=True,text=True).stdout)
        assert abs(fd - edited_s) < 0.1 + 0.034 * nparts, (fd, edited_s, nparts)  # the editor's hand cut is in the final video
        reel = out_dir / "Reel 01 - The heart.mp4"
        rd = sp.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0",str(reel)],capture_output=True,text=True).stdout.strip()
        assert rd == "720,1280", rd
        sp.run(["ffmpeg","-v","error","-y","-ss","4","-i",str(out_dir/"Edited - full video.mp4"),"-frames:v","1","-vf","scale=640:-2",str(OUT/"r-cover.png")])
        sp.run(["ffmpeg","-v","error","-y","-ss","20","-i",str(out_dir/"Edited - full video.mp4"),"-frames:v","1","-vf","scale=640:-2",str(OUT/"r-main.png")])
        sp.run(["ffmpeg","-v","error","-y","-ss","3","-i",str(reel),"-frames:v","1","-vf","scale=360:-2",str(OUT/"r-reel.png")])
        assert (out_dir / "Edited - chapters.txt").read_text().startswith("0:00"), "chapters file"
        for extra in ("Edited - full video (WhatsApp).mp4", "Edited - full video (square).mp4"):
            assert (out_dir / extra).exists(), extra
        assert (out_dir / "Edited - full video.srt").exists()
        for f in ("Edited - full video.vtt", "Edited - YouTube.txt", "Edited - timeline for DaVinci Resolve.fcpxml",
                  "Edited - cut list.edl", "Edited - project.json"):
            assert (out_dir / f).exists(), f
        assert "0:00 " in (out_dir / "Edited - YouTube.txt").read_text()
        # 8. reload: everything comes back (paths, sync, preview reloaded from the engine)
        page.goto(APP)
        page.reload()
        wait_text(page, "#engStatus", "for video", 60)
        page.wait_for_function("STU.wavV && STU.wavA", timeout=60000)
        page.click("#edClose")
        assert "Talk video.mov" in page.locator("#engVideoPath").inner_text()
        page.locator("#stuSync").scroll_into_view_if_needed()
        shot(page, "09-after-reload")
        assert not errors, errors
        print("UI TEST PASSED:", ", ".join(log))
        print("QC:\n" + qc)
        b.close()
finally:
    if eng:
        eng.terminate()
