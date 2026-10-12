"""Stage 1 acceptance tests on synthetic media with a known answer. Needs ffmpeg; uses deep-filter if
STUDIO_DEEP_FILTER points to it (otherwise the sound tests run with voice isolation switched off)."""
import os
import subprocess
import time
from pathlib import Path

import numpy as np

from .conftest import STRENGTH, params, probe, run


def test_report(eng, media):
    r = run(eng, "report", {"video": media["video"], "audio": media["audio"]})
    text = " ".join(n["text"] for n in r["notes"])
    assert "HDR video (HLG)" in text
    assert "Variable frame rate" in text
    assert "shorter than the video" in text
    assert r["video"]["video"]["width"] == 360 and r["video"]["video"]["height"] == 640
    assert r["audio"]["levels"]["lufs"] is not None


def test_prepare(prepared, media):
    px = probe(prepared["proxy"])
    v = next(s for s in px["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (540, 960)
    assert v["avg_frame_rate"] == v["r_frame_rate"] == "30/1"  # steady frame rate
    assert v.get("color_transfer") in (None, "bt709")  # HDR converted for normal screens
    assert abs(float(px["format"]["duration"]) - media["video_s"]) < 0.2
    for k in ("video_wav", "audio_wav"):
        assert Path(prepared[k]).stat().st_size > 1000


def test_sync_accuracy(synced, media):
    """Acceptance: offset + drift recovered; error < 5 ms at start, middle and end."""
    A, B = media["A"], media["B"]
    for tv in (5.0, media["video_s"] / 2, media["video_s"] - 8):
        err = abs((synced["a"] + synced["b"] * tv) - (A + B * tv)) * 1000
        assert err < 5, f"sync error {err:.2f} ms at {tv}s (got a={synced['a']}, b={synced['b']})"
    assert synced["confident"]


def lag_ms(ref: np.ndarray, y: np.ndarray, rate: int) -> float:
    """ref starts 1 s before y; returns how late y is compared with ref (0 = perfectly aligned)."""
    from studio_engine.syncer import xcorr
    lag, conf = xcorr(y, ref, 0, len(ref) - len(y), phat=0.8)
    return (rate - lag) / rate * 1000


def test_clean_alignment_and_loudness(eng, media, synced):
    r = run(eng, "clean", params(media, synced))
    out = Path(r["clean_audio"])
    info = probe(out)
    a = info["streams"][0]
    assert a["sample_rate"] == "48000" and a["channels"] == 2
    vdur = float(probe(media["video"])["format"]["duration"])
    assert abs(float(info["format"]["duration"]) - vdur) < 0.001  # exactly the video's length
    assert abs(r["output_lufs"] - (-16)) < 1.0 and r["output_tp"] <= -1.4
    # the cleaned sound must line up with the camera sound (sync + drift + no filter delay)
    from studio_engine.syncer import read_window
    cam = Path(media["video"]).parent / "studio_work" / "video_16k.wav"
    tmp16 = out.with_name("clean16.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-ac", "1", "-ar", "16000", tmp16], check=True)
    for t in (20.0, 75.0, 130.0):
        y, rate, _ = read_window(tmp16, t, 8)
        x, _, _ = read_window(cam, t - 1, 10)
        assert abs(lag_ms(x, y, rate)) < 5, f"cleaned audio off by {lag_ms(x, y, rate):.2f} ms at {t}s"
    # parts of the video with no recorder sound are filled (room tone or quiet), never garbage
    r2 = run(eng, "clean", params(media, synced))
    assert r2.get("cached")  # second run reuses the finished result


def test_preview(eng, media, synced):
    r = run(eng, "preview", params(media, synced, start=40))
    for k in ("before", "after"):
        d = float(probe(r[k])["format"]["duration"])
        assert 29.9 < d < 30.1


def keep_list(total):
    return [[0.0, 22.4], [25.1, 61.0], [63.3, 101.7], [104.0, total - 1]]


def test_render_and_qc(eng, media, synced):
    total = media["video_s"]
    ass = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,70,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,1,2,80,80,420,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.50,0:00:04.00,Default,,0,0,0,,Testing captions\n"
    p = params(media, synced, keep=keep_list(total), srt="1\n00:00:00,000 --> 00:00:02,000\nhello\n",
               settings={"res": "1080", "enc": "auto", "reelRes": "720", "reelCaps": True},
               reels=[{"name": "Reel 01 - Test", "start": 30, "end": 50, "segs": [[30.0, 50.0]], "ass": ass}])
    r = run(eng, "render", p)
    qc = r["qc"]
    measured = [l for l in qc["lipsync"] if l.get("ok") is not None]
    assert len(measured) >= 2, qc
    for l in measured:
        assert abs(l["offset_ms"]) < 5, qc["text"]  # acceptance: < 5 ms on synthetic
    assert qc["duration_ok"] and qc["loudness_ok"], qc["text"]
    final = Path(r["outputs"][0])
    v = next(s for s in probe(final)["streams"] if s["codec_type"] == "video")
    assert v["height"] == 1920 and v["width"] == 1080 and v["avg_frame_rate"] == "30/1"
    reel = Path(r["outputs"][-1])
    rv = probe(reel)
    assert abs(float(rv["format"]["duration"]) - 20.0) < 0.1
    assert (final.parent / "Edited - quality check.txt").exists()
    assert (final.parent / "Edited - full audio.mp3").exists()


def test_cancel_and_resume(eng, media, synced):
    """Interrupt a render, then Resume: finished parts are kept, the result is complete."""
    m = eng.manager()
    p = params(media, synced, keep=[[0, 30], [31, 60], [61, 90], [91, 120]],
               settings={"res": "orig", "enc": "auto"})
    part_dir = Path(media["video"]).parent / "studio_work" / "render" / "full"
    import shutil
    shutil.rmtree(part_dir, ignore_errors=True)  # start clean (before the job starts): earlier tests leave parts here
    job = m.submit("render", p)
    t0 = time.time()
    while not any(part_dir.glob("part_0001.mp4.done.json")) or not (part_dir / "part_0001.mp4").exists():
        time.sleep(0.05)
        assert time.time() - t0 < 300
    first = (part_dir / "part_0001.mp4").stat().st_mtime_ns
    m.cancel(job.id)
    while job.state == "running":
        time.sleep(0.05)
    assert job.state in ("cancelled", "done")
    r = run(eng, "render", p)
    assert (part_dir / "part_0001.mp4").stat().st_mtime_ns == first  # not made again
    assert r["qc"]["duration_ok"]


def test_server_security(eng, media, prepared):
    from fastapi.testclient import TestClient
    c = TestClient(eng.app, base_url="http://127.0.0.1:8765")
    assert c.get("/api/hello").json()["paired"] is False
    assert c.get("/api/hello", headers={"x-engine-token": "test-token"}).json()["paired"] is True
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/jobs", headers={"x-engine-token": "wrong"}).status_code == 401
    assert c.get("/api/jobs", headers={"x-engine-token": "test-token"}).status_code == 200
    # only our website may call it, and Chrome's private-network question is answered
    pre = c.options("/api/jobs", headers={"Origin": "https://psm.dailyquotecards.workers.dev",
                                          "Access-Control-Request-Method": "POST",
                                          "Access-Control-Request-Headers": "x-engine-token,content-type",
                                          "Access-Control-Request-Private-Network": "true"})
    assert pre.headers.get("access-control-allow-origin") == "https://psm.dailyquotecards.workers.dev"
    assert pre.headers.get("access-control-allow-private-network") == "true"
    bad = c.options("/api/jobs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    # a website that tricks DNS into pointing at this computer is refused
    assert TestClient(eng.app, base_url="http://evil.example:8765").get("/api/hello").status_code == 403
    # files: only engine-made files, with byte ranges for video seeking
    proxy = prepared["proxy"]
    r = c.get("/api/file", params={"path": proxy, "t": "test-token"}, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and len(r.content) == 100
    assert c.get("/api/file", params={"path": media["video"], "t": "test-token"}).status_code == 404
    hw = c.get("/api/hardware", headers={"x-engine-token": "test-token"}).json()
    assert hw["best_encoder"] and hw["summary"]


def test_hdr_to_normal_keeps_brightness_and_colour(tmp_path):
    """A normal picture turned into real iPhone-style HDR (HLG) must come back looking like the original."""
    from studio_engine import ff, media
    src, hlg = tmp_path / "src.png", tmp_path / "hlg.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptehdbars=s=640x360", "-frames:v", "1",
                    src], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
                    "format=gbrpf32le,zscale=tin=iec61966-2-1:t=linear:npl=100,zscale=p=bt2020:t=arib-std-b67:"
                    "m=bt2020nc:r=tv:pin=bt709:min=gbr,format=yuv420p10le", "-c:v", "libx265", "-x265-params",
                    "log-level=error", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace",
                    "bt2020nc", "-frames:v", "1", hlg], check=True)
    v = media.stream(ff.probe(hlg), "video")
    assert media.is_hdr(v) == "HLG"
    raw = lambda args: np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", *args, "-f", "rawvideo", "-pix_fmt",
                                                     "rgb24", "-"], capture_output=True).stdout, np.uint8).astype(float)
    a = raw(["-i", str(src)])
    b = raw(["-i", str(hlg), "-vf", media.tonemap_chain(v)])
    assert len(a) == len(b)
    assert abs(b.mean() - a.mean()) < 12, (a.mean(), b.mean())   # not darker / brighter
    assert np.abs(b - a).mean() < 15                              # colours close to the original


def test_opens_in_google_chrome(tmp_path, monkeypatch):
    """The website opens in Google Chrome when it is installed (Windows paths), else the default browser."""
    from studio_engine import config, server
    calls = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: calls.append(("chrome", cmd)))
    monkeypatch.setattr(server.webbrowser, "open", lambda link: calls.append(("default", link)))
    monkeypatch.setattr(config, "IS_WIN", True)
    monkeypatch.setattr(config, "IS_MAC", False)
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    server.open_site("https://example/#engine=x")
    assert calls[-1] == ("chrome", [str(exe), "https://example/#engine=x"])
    exe.unlink()
    for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(k, str(tmp_path / "none"))
    server.open_site("https://example/#engine=y")
    assert calls[-1] == ("default", "https://example/#engine=y")


def test_fastest_decoder_is_measured_once(eng, media, monkeypatch):
    """On Windows the engine times Quick Sync / D3D11 / DXVA2 / processor on the real video and remembers the
    winner. (Here the graphics-chip ones are not available, so the processor must be chosen.)"""
    from studio_engine import config, media as M
    monkeypatch.setitem(M.DECODERS, "other", M.DECODERS["win"])  # the Windows choices (none exist on this test machine)

    class FakeCtx:
        logs = []
        def check(self): pass
        def progress(self, *a): pass
        def log(self, t): self.logs.append(t)
    st = config.load_settings(); st.pop("decoders", None); config.save_settings(st)
    ctx = FakeCtx()
    assert M.best_decoder(ctx, media["video"]) == []
    assert "Decoder test" in ctx.logs[-1] and "processor:" in ctx.logs[-1]
    assert list(config.load_settings()["decoders"].values()) == [[]]
    ctx.logs.clear()
    assert M.best_decoder(ctx, media["video"]) == [] and not ctx.logs  # remembered, not measured again
    assert M.proxy_fps({"avg_frame_rate": "60/1"}) == "30" and M.proxy_fps({"avg_frame_rate": "60000/1001"}) == "30000/1001"
    assert M.proxy_fps({"avg_frame_rate": "30/1"}) == "30"


def test_keep_whole_recording(eng, media, synced):
    """The recorder started 3.2 s before the camera: with 'keep the whole recording' the finished video starts
    with those 3.2 s of speech (picture filled), and the rest stays in lip sync."""
    total, A = media["video_s"], media["A"]
    pre = A / media["B"]
    p = params(media, synced, keep_all=True, keep=[[-pre, 30.0], [33.0, total - 1]],
               settings={"res": "orig", "enc": "auto", "fill": "title", "fill_title": "Before the camera"})
    r = run(eng, "render", p)
    qc = r["qc"]
    assert qc["duration_ok"], qc["text"]
    final = Path(r["outputs"][0])
    dur = float(probe(final)["format"]["duration"])
    assert abs(dur - (pre + 30.0 + (total - 1 - 33.0))) < 0.1, dur
    measured = [l for l in qc["lipsync"] if l.get("ok") is not None]
    assert measured and all(abs(l["offset_ms"]) < 5 for l in measured), qc["text"]
    # the first seconds of the finished sound are the recorder's speech from before the camera started
    from studio_engine.syncer import xcorr
    out16 = final.with_name("ka16.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", final, "-t", "3", "-ac", "1", "-ar", "16000", out16], check=True)
    rec16 = final.with_name("rec16.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", media["audio"], "-t", "6", "-ac", "1", "-ar", "16000", rec16], check=True)
    from studio_engine.syncer import read_window
    y, rate, _ = read_window(out16, 0.5, 2.0)
    x, _, _ = read_window(rec16, 0.0, 5.0)
    lag, conf = xcorr(y, x, 0, len(x) - len(y), phat=0.8)
    start_v = round(-pre * 30) / 30  # the finished video starts at this video time (whole frames)
    want = A + media["B"] * (start_v + 0.5)  # recorder time heard at output second 0.5
    assert conf >= 4 and abs(lag / rate - want) < 0.003, (lag / rate, want, conf)
    edl = (final.parent / "Edited - cut list.edl").read_text()
    assert "BL" in edl.splitlines()[3]  # first event: no camera picture
    xml = (final.parent / "Edited - timeline for DaVinci Resolve.fcpxml").read_text()
    assert "<gap " in xml
