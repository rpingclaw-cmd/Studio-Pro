"""Sound: sync the recorder to the camera, voice isolation (DeepFilterNet), room tone, EQ, de-esser,
gentle compression and two-pass loudness. Also the quick 30-second before/after preview.

Everything here keeps the exact timing: the cleaned audio is on the VIDEO's timeline and has exactly
the video's length, so later cuts line up with the picture.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from . import config, ff, hardware, media
from .jobs import Cancelled, Ctx, JobError, file_sig, key_of, mark_done, read_done, step_done
from .wavio import Writer, open_pcm16

RATE = 48000
# The voice goes to both channels at full level ("dual mono"); loudness is measured on that stereo signal,
# which is the usual podcast convention (-16 LUFS stereo). ffmpeg's plain mono->stereo would lower it by 3 dB.
DUAL_MONO = "pan=stereo|c0=c0|c1=c0"
HI_RATE = 384000  # drift correction runs at this rate: rounding error < 1.3 ppm (< 5 ms per hour)

DEFAULTS = {"strength": 60, "lufs": -16, "hp": True, "presence": True, "deess": True, "comp": True,
            "roomtone": True}


def settings(p: dict) -> dict:
    s = {**DEFAULTS, **(p.get("sound") or {})}
    s["strength"] = max(0, min(100, int(s["strength"])))
    s["lufs"] = -14 if int(s["lufs"]) == -14 else -16
    return s


def atten_db(strength: int) -> float:
    """Slider 0-100 -> DeepFilterNet attenuation limit. 100 = remove all noise; lower keeps some natural room."""
    return 100.0 if strength >= 100 else round(6 + 0.45 * strength, 1)


def sync_filter(a: float, b: float, src_rate: int) -> str:
    """Audio time = a + b * video time  ->  shift by a, then change speed by b (sample-exact resampling)."""
    parts = []
    if a >= 0:
        parts.append(f"atrim=start={a:.6f},asetpts=PTS-STARTPTS")
    else:
        parts.append(f"adelay=delays={int(round(-a * src_rate))}S:all=1")
    if abs(b - 1) > 1e-7:
        parts.append(f"aresample={HI_RATE},asetrate={int(round(HI_RATE * b))}")
    parts.append(f"aresample={RATE}")
    return ",".join(parts)


def polish_chain(s: dict) -> str:
    f = []
    if s["hp"]:
        f.append("highpass=f=80:p=2")
    if s["presence"]:
        f += ["equalizer=f=220:t=q:w=1.0:g=-1.5", "equalizer=f=3500:t=q:w=1.2:g=2.5"]
    if s["deess"]:
        f.append("deesser=i=0.35:m=0.5:f=0.5:s=o")
    if s["comp"]:
        f.append("acompressor=threshold=-22dB:ratio=2.5:attack=10:release=150:makeup=2:knee=4")
    return ",".join(f) or "anull"


def _source(p: dict) -> tuple[Path, bool]:
    if p.get("audio"):
        return media._need(p["audio"], "separate audio"), True
    return media._need(p.get("video"), "video"), False


# ---------- step 1: synced, full length, 48 kHz mono ----------
def make_synced(ctx: Ctx, p: dict, wd: Path, vdur: float) -> Path:
    src, separate = _source(p)
    sync = p.get("sync") or {}
    a, b = (float(sync.get("a", 0)), float(sync.get("b", 1))) if separate else (0.0, 1.0)
    out = wd / "synced_48k.wav"
    key = key_of({"src": file_sig(src), "a": round(a, 6), "b": round(b, 9), "dur": round(vdur, 3), "v": 1})
    if step_done(out, key):
        return out
    info = ff.probe(src)
    ast = media.stream(info, "audio")
    if not ast:
        raise JobError(f"{src.name} has no sound track.")
    src_rate = int(ast.get("sample_rate") or RATE)
    af = sync_filter(a, b, src_rate) + f",apad=whole_dur={vdur:.6f},atrim=end={vdur:.6f}"
    tmp = ff.tmp_of(out)
    ff.run_ff(ctx, ["-i", src, "-map", "0:a:0", "-vn", "-af", af, "-ac", "1", "-ar", RATE, "-c:a", "pcm_s16le", tmp],
              duration=vdur, msg="Lining up the recorder with the camera…")
    ff.finish(tmp, out)
    mark_done(out, key)
    return out


# ---------- level analysis (50 ms frames) ----------
def frame_db(path, frame_s: float = 0.05) -> tuple[np.ndarray, int]:
    arr, rate, ch = open_pcm16(path)
    hop = int(rate * frame_s)
    n = arr.shape[0] // hop
    out = np.empty(n, np.float32)
    step = 20000
    for i in range(0, n, step):
        j = min(n, i + step)
        x = np.asarray(arr[i * hop:j * hop], dtype=np.float32) / 32768.0
        x = x.mean(axis=1) if ch > 1 else x[:, 0]
        out[i:j] = 10 * np.log10(1e-12 + (x.reshape(j - i, hop) ** 2).mean(axis=1))
    return out, hop


def quietest_window(db: np.ndarray, frames: int) -> int | None:
    """Start frame of the quietest stretch that still has some sound (not padding / digital silence)."""
    if len(db) < frames + 2:
        return None
    live = db > -85
    p = np.where(live, 10 ** (db / 10), np.inf)
    c = np.cumsum(np.concatenate([[0.0], np.where(np.isfinite(p), p, 0.0)]))
    bad = np.cumsum(np.concatenate([[0], (~live).astype(np.int64)]))
    sums = c[frames:] - c[:-frames]
    nbad = bad[frames:] - bad[:-frames]
    sums[nbad > 0] = np.inf
    if not np.isfinite(sums).any():
        return None
    return int(np.argmin(sums))


# ---------- step 2: DeepFilterNet in parallel chunks ----------
CHUNK_S = float(os.environ.get("STUDIO_DFN_CHUNK_S", "300"))
PRE_S, XF_S = 2.0, 0.25


def deep_filter(ctx: Ctx, src: Path, out: Path, atten: float, key: str):
    if step_done(out, key):
        return
    exe = ff.tool("deep-filter")
    arr, rate, ch = open_pcm16(src)
    total = arr.shape[0]
    chunk, pre, xf = int(CHUNK_S * rate), int(PRE_S * rate), int(XF_S * rate)
    cdir = out.parent / "dfn_chunks"
    cdir.mkdir(exist_ok=True)
    jobs = []
    for i, s in enumerate(range(0, total, chunk)):
        a = max(0, s - pre)
        b = min(total, s + chunk + xf)
        jobs.append({"i": i, "s": s, "a": a, "b": b, "inp": cdir / f"in_{i:03d}.wav", "outdir": cdir / f"out_{i:03d}",
                     "key": key_of([key, a, b])})
    todo = [j for j in jobs if not step_done(j["outdir"] / j["inp"].name, j["key"])]
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    running: list[tuple[dict, subprocess.Popen]] = []
    done_n = len(jobs) - len(todo)
    ctx.progress(done_n / len(jobs), f"Voice isolation (DeepFilterNet): part {done_n} of {len(jobs)}…")
    flags = 0x00004000 if config.IS_WIN else 0  # below-normal priority, stays attached to the engine window
    try:
        while todo or running:
            while todo and len(running) < workers:
                j = todo.pop(0)
                w = Writer(j["inp"], rate, 1)
                w.write_i16(np.asarray(arr[j["a"]:j["b"], 0]))
                w.close()
                if j["outdir"].exists():
                    shutil.rmtree(j["outdir"])
                cmd = [exe, "-D", "-a", f"{atten}", "-o", str(j["outdir"]), str(j["inp"])]
                ctx.log("RUN " + " ".join(cmd))
                running.append((j, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                    creationflags=flags)))
            time.sleep(0.2)
            if ctx.job.cancel_flag.is_set():
                raise Cancelled()
            for j, pr in list(running):
                if pr.poll() is None:
                    continue
                running.remove((j, pr))
                text = pr.stdout.read().decode("utf-8", "replace") if pr.stdout else ""
                res = j["outdir"] / j["inp"].name
                if pr.returncode != 0 or not res.exists():
                    ctx.log(text[-3000:])
                    raise JobError("Voice isolation (DeepFilterNet) stopped with an error. Details are in the job log.")
                mark_done(res, j["key"])
                j["inp"].unlink(missing_ok=True)
                done_n += 1
                ctx.progress(done_n / len(jobs), f"Voice isolation (DeepFilterNet): part {done_n} of {len(jobs)}…")
    finally:
        for _, pr in running:
            if pr.poll() is None:
                pr.terminate()
    # stitch: each part starts PRE_S early (model warm-up) and overlaps the next by XF_S (cross-fade)
    tmp = ff.tmp_of(out)
    w = Writer(tmp, rate, 1)
    tail = None
    for j in jobs:
        y, r2, _ = open_pcm16(j["outdir"] / j["inp"].name)
        y = np.asarray(y[:, 0], dtype=np.float32) / 32768.0
        need = j["b"] - j["a"]
        if len(y) < need:  # deep-filter ends ~30 ms early
            y = np.concatenate([y, np.zeros(need - len(y), np.float32)])
        seg = y[j["s"] - j["a"]:need]
        if tail is not None:
            n = min(len(tail), len(seg))
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
            seg = seg.copy()
            seg[:n] = tail[:n] * (1 - ramp) + seg[:n] * ramp
        is_last = j is jobs[-1]
        body_end = len(seg) if is_last else len(seg) - xf
        w.write(seg[:body_end])
        tail = None if is_last else seg[body_end:]
    w.close()
    ff.finish(tmp, out)
    mark_done(out, key)
    shutil.rmtree(cdir, ignore_errors=True)


# ---------- step 3: room tone bed ----------
def make_bed(raw: Path, cleaned: Path, out: Path, s: dict) -> dict:
    """A 20-second loop of this room's own quiet sound, used under the voice so pauses never go 'dead'."""
    db_raw, hop = frame_db(raw)
    db_cl, _ = frame_db(cleaned)
    live = db_cl[db_cl > -85]
    if len(live) < 100:
        return {"bed": False}
    speech = float(np.percentile(live, 90))
    floor = float(np.percentile(live, 10))
    target = speech - 48.0
    info = {"speech_db": round(speech, 1), "floor_db": round(floor, 1), "bed": False}
    if not s["roomtone"] or floor >= target - 3:
        return info  # enough natural room sound already
    st = quietest_window(db_raw, int(3.0 / 0.05))
    if st is None:
        return info
    arr, rate, _ = open_pcm16(raw)
    x = np.asarray(arr[st * hop: st * hop + 3 * rate, 0], dtype=np.float32) / 32768.0
    x = x - x.mean()
    rms_db = 10 * np.log10(1e-12 + float((x ** 2).mean()))
    if rms_db < -85:
        return info
    # build a 20 s loop from overlapping copies with equal-power cross-fades (no audible seams)
    L, xf = len(x), int(0.5 * rate)
    fade = np.sin(np.linspace(0, np.pi / 2, xf, dtype=np.float32))
    bed = np.zeros(20 * rate + xf, np.float32)
    pos, k = 0, 0
    while pos < len(bed):
        seg = x.copy() if k % 2 == 0 else x[::-1].copy()
        seg[:xf] *= fade
        seg[-xf:] *= fade[::-1]
        n = min(len(seg), len(bed) - pos)
        bed[pos:pos + n] += seg[:n]
        pos += L - xf
        k += 1
    loop = bed[xf:20 * rate + xf].copy()
    loop[:xf] = loop[:xf] * fade + bed[20 * rate:20 * rate + xf] * fade[::-1]  # seamless wrap
    gain = 10 ** ((target - rms_db) / 20)
    w = Writer(out, rate, 1)
    w.write(np.clip(loop * gain, -1, 1))
    w.close()
    info.update({"bed": True, "bed_db": round(target, 1), "from_s": round(st * 0.05, 1)})
    return info


# ---------- step 4: polish + loudness (measure, exact gain, true-peak limiter) ----------
def limiter(tp_db: float = -1.5) -> str:
    """True-peak limiter: works at 4x the sample rate so peaks between samples are caught too.
    latency=1 removes the limiter's look-ahead delay, so timing stays exact."""
    lim = 10 ** ((tp_db - 0.3) / 20)
    return (f"aresample=192000,alimiter=limit={lim:.5f}:attack=1.5:release=60:level=false:latency=1,"
            f"aresample={RATE}")


def loud_args(target: float, vdur: float, gain_db: float) -> str:
    return f"volume={gain_db:.2f}dB,{limiter()},apad=whole_dur={vdur:.6f},atrim=end={vdur:.6f}"


def parse_ebur(text: str) -> dict:
    import re
    i = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", text)
    tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", text)
    num = lambda v: float(v) if v and v != "-inf" else -70.0
    return {"lufs": num(i[-1] if i else None), "true_peak": num(tp[-1] if tp else None)}


def polish_and_loudness(ctx: Ctx, cleaned: Path, bed: Path | None, out: Path, s: dict, vdur: float, wd: Path) -> dict:
    polished = wd / "polished_48k.wav"
    tmp = ff.tmp_of(polished)
    if bed:
        head = f"[1:a]atrim=end={vdur:.6f}[b];[0:a][b]amix=inputs=2:normalize=0:duration=first,"
        args = ["-i", cleaned, "-stream_loop", "-1", "-i", bed]
    else:
        head, args = "[0:a]", ["-i", cleaned]
    graph = f"{head}{polish_chain(s)},{DUAL_MONO},asplit=2[o][m];[m]ebur128=peak=true,anullsink"
    text = ff.run_ff(ctx, args + ["-filter_complex", graph, "-map", "[o]", "-c:a", "pcm_s16le", tmp],
                     duration=vdur, msg="EQ, de-esser and compression; measuring loudness…", loglevel="info",
                     want_stderr=True)
    ff.finish(tmp, polished)
    m = parse_ebur(text)
    gain = s["lufs"] - m["lufs"]
    tmp2 = ff.tmp_of(out)
    tried: list[tuple[float, float]] = []
    for attempt in range(4):  # small corrections if the limiter took away more than 0.5 LU
        graph = f"[0:a]{loud_args(s['lufs'], vdur, gain)},asplit=2[o][m];[m]ebur128=peak=true,anullsink"
        text2 = ff.run_ff(ctx, ["-i", polished, "-filter_complex", graph, "-map", "[o]", "-ar", RATE,
                                "-c:a", "pcm_s16le", tmp2], duration=vdur,
                          msg=f"Setting loudness to {s['lufs']} LUFS…", loglevel="info", want_stderr=True)
        m2 = parse_ebur(text2)
        tried.append((gain, m2["lufs"]))
        ctx.log(f"loudness pass {attempt + 1}: gain {gain:+.2f} dB -> {m2['lufs']} LUFS, true peak {m2['true_peak']}")
        if abs(m2["lufs"] - s["lufs"]) <= 0.5 or gain >= 30:
            break
        if len(tried) >= 2 and tried[-1][1] != tried[-2][1]:  # aim using the last two results
            (g1, l1), (g2, l2) = tried[-2], tried[-1]
            slope = max(0.15, (l2 - l1) / (g2 - g1)) if g2 != g1 else 1.0
            gain = min(30.0, g2 + (s["lufs"] - l2) / slope)
        else:
            gain = min(30.0, gain + s["lufs"] - m2["lufs"])
    ff.finish(tmp2, out)
    polished.unlink(missing_ok=True)
    return {"measured_lufs": m["lufs"], "gain_db": round(gain, 2), "output_lufs": m2["lufs"],
            "output_tp": m2["true_peak"]}


def ensure_clean(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    vinfo = ff.probe(video)
    vdur = media.duration(vinfo)
    wd = media.work_dir(str(video))
    s = settings(p)
    out = wd / "clean_audio.wav"
    src, separate = _source(p)
    key = key_of({"src": file_sig(src), "sync": p.get("sync") if separate else None, "s": s, "dur": round(vdur, 3),
                  "v": 4})
    if step_done(out, key):
        return read_done(out).get("info", {}) | {"clean_audio": str(out), "cached": True}
    hardware.disk_check(wd, 0.4 + vdur / 3600 * 2.2, "cleaning the sound")
    ctx.stage(0.0, 0.15)
    synced = make_synced(ctx, p, wd, vdur)
    cleaned = synced
    if s["strength"] > 0:
        if not config.find_tool("deep-filter"):
            raise JobError("Voice isolation (DeepFilterNet) is not installed. Run the Studio Engine installer again, "
                           "or set the strength slider to 0.")
        ctx.stage(0.15, 0.6)
        cleaned = wd / "dfn_48k.wav"
        deep_filter(ctx, synced, cleaned, atten_db(s["strength"]), key_of([key, "dfn"]))
    ctx.stage(0.6, 0.65)
    ctx.progress(0, "Finding the room's quiet sound (room tone)…")
    bed_path = wd / "roomtone.wav"
    bed = make_bed(synced, cleaned, bed_path, s)
    ctx.stage(0.65, 1.0)
    lv = polish_and_loudness(ctx, cleaned, bed_path if bed.get("bed") else None, out, s, vdur, wd)
    info = {**lv, "room_tone": bed, "strength": s["strength"], "atten_db": atten_db(s["strength"]) if s["strength"] else 0}
    mark_done(out, key, {"info": info})
    return info | {"clean_audio": str(out), "cached": False}


def job_clean(ctx: Ctx, p: dict) -> dict:
    r = ensure_clean(ctx, p)
    rt = r.get("room_tone") or {}
    r["message"] = (f"Sound cleaned: loudness {r['output_lufs']:.1f} LUFS, peak {r['output_tp']:.1f} dB"
                    + (", room tone added under pauses" if rt.get("bed") else "") + ".")
    return r


# ---------- 30-second before / after ----------
def job_preview(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    wd = media.work_dir(str(video)) / "ab"
    wd.mkdir(exist_ok=True)
    s = settings(p)
    src, separate = _source(p)
    sync = p.get("sync") or {}
    a, b = (float(sync.get("a", 0)), float(sync.get("b", 1))) if separate else (0.0, 1.0)
    t = max(0.0, float(p.get("start", 0)))
    length, pre = 30.0, 3.0
    key = key_of({"src": file_sig(src), "a": a, "b": b, "t": t, "s": s, "v": 1})
    before, after = wd / f"before_{key}.wav", wd / f"after_{key}.wav"
    if not (before.exists() and after.exists()):
        for old in wd.glob("*.wav"):
            try:
                old.unlink()
            except OSError:  # Windows: the browser may still be playing it
                pass
        ta = a + b * (t - pre)
        raw = wd / "raw.wav"
        lead = max(0.0, -ta)
        af = (f"adelay=delays={int(lead * 1000)}:all=1," if lead else "") + f"aresample={RATE},atrim=end={pre + length}"
        ctx.stage(0, 0.2)
        ff.run_ff(ctx, ["-ss", f"{max(0.0, ta):.3f}", "-i", src, "-map", "0:a:0", "-vn", "-af", af, "-ac", "1",
                        "-ar", RATE, "-c:a", "pcm_s16le", raw], duration=pre + length, msg="Cutting 30 seconds…")
        cleaned = raw
        if s["strength"] > 0:
            ctx.stage(0.2, 0.75)
            ctx.progress(0, "Voice isolation on the preview…")
            outdir = wd / "dfn"
            ff.run_tool(ctx, [ff.tool("deep-filter"), "-D", "-a", f"{atten_db(s['strength'])}", "-o", str(outdir), str(raw)],
                        "Voice isolation on the preview…", expect_seconds=8)
            cleaned = outdir / raw.name
        ctx.stage(0.75, 1.0)
        bed = wd / "bed.wav"
        has_bed = make_bed(raw, cleaned, bed, s).get("bed")
        trim = f"atrim=start={pre},asetpts=PTS-STARTPTS"
        mid_a, mid_b = wd / "mid_after.wav", wd / "mid_before.wav"
        if has_bed:
            graph = (f"[1:a]atrim=end={pre + length}[b];[0:a][b]amix=inputs=2:normalize=0:duration=first,"
                     f"{polish_chain(s)},{trim}")
            ff.run_ff(ctx, ["-i", cleaned, "-stream_loop", "-1", "-i", bed, "-filter_complex", graph,
                            "-c:a", "pcm_s16le", mid_a], duration=length, msg="Making the 'after' sound…")
        else:
            ff.run_ff(ctx, ["-i", cleaned, "-af", f"{polish_chain(s)},{trim}", "-c:a", "pcm_s16le", mid_a],
                      duration=length, msg="Making the 'after' sound…")
        ff.run_ff(ctx, ["-i", raw, "-af", trim, "-c:a", "pcm_s16le", mid_b], duration=length)
        # both at the same loudness, so the comparison is fair (louder always sounds 'better')
        for mid, dst in ((mid_a, after), (mid_b, before)):
            m = parse_ebur(ff.run_ff(ctx, ["-i", mid, "-af", f"{DUAL_MONO},ebur128=peak=true", "-f", "null", "-"],
                                     loglevel="info", want_stderr=True))
            ff.run_ff(ctx, ["-i", mid, "-af", f"{DUAL_MONO}," + loud_args(s["lufs"], length, s["lufs"] - m["lufs"]),
                            "-ar", RATE, "-c:a", "pcm_s16le", dst], duration=length, msg="Setting loudness…")
            mid.unlink(missing_ok=True)
        shutil.rmtree(wd / "dfn", ignore_errors=True)
        for f in (raw, bed):
            f.unlink(missing_ok=True)
    return {"before": str(before), "after": str(after), "start": t,
            "message": "Preview ready: listen to Before and After (same loudness)."}
