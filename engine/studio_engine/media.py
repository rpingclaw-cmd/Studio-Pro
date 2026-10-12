"""Media report (like MediaInfo, in plain words) and Prepare (sound for sync + small preview video)."""
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np

from . import config, ff, hardware
from .jobs import Ctx, JobError, file_sig, key_of, mark_done, step_done


def work_dir(video: str) -> Path:
    d = Path(video).resolve().parent / "studio_work"
    d.mkdir(exist_ok=True)
    return d


def _need(path: str | None, what: str) -> Path:
    if not path:
        raise JobError(f"Choose the {what} first.")
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise JobError(f"Can't find the {what}: {path}. Was it moved or renamed?")
    return p


def _rate(s: str | None) -> float:
    try:
        a, b = (s or "0/1").split("/")
        return float(a) / float(b or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def stream(info: dict, kind: str) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == kind
                 and not (s.get("disposition") or {}).get("attached_pic")), None)


def duration(info: dict) -> float:
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        v = stream(info, "video") or stream(info, "audio") or {}
        return float(v.get("duration") or 0)


def rotation(v: dict) -> int:
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    try:
        return -int((v.get("tags") or {}).get("rotate", 0))
    except ValueError:
        return 0


def is_hdr(v: dict | None) -> str | None:
    if not v:
        return None
    t = v.get("color_transfer") or ""
    return "HLG" if t == "arib-std-b67" else "PQ (HDR10)" if t == "smpte2084" else None


def fps(v: dict | None) -> float:
    f = _rate((v or {}).get("avg_frame_rate")) or _rate((v or {}).get("r_frame_rate")) or 30.0
    if not (1 < f < 241):
        f = 30.0
    return round(f) if abs(f - round(f)) < 0.02 else round(f, 3)


def output_fps(v: dict | None) -> str:
    """Steady frame rate used for everything the engine makes (exact fraction for 29.97 etc.)."""
    f = fps(v)
    for exact, frac in ((23.976, "24000/1001"), (29.97, "30000/1001"), (59.94, "60000/1001")):
        if abs(f - exact) < 0.01:
            return frac
    return str(int(round(f)))


def vfr_check(path, v: dict) -> dict:
    """Look at the first 30 seconds of frame times: is the frame rate steady?"""
    cmd = [ff.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+30",
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000 if config.IS_WIN else 0)
    ts = sorted(float(x) for x in re.findall(r"^-?[\d.]+", r.stdout, re.M))
    d = np.diff(np.array(ts)) if len(ts) > 3 else np.array([])
    d = d[d > 0]
    if len(d) < 3:
        return {"vfr": False}
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    avg, rfr = _rate(v.get("avg_frame_rate")), _rate(v.get("r_frame_rate"))
    vfr = bool(hi / lo > 1.15 or (avg and rfr and abs(avg - rfr) / rfr > 0.01))
    return {"vfr": vfr, "min_fps": round(1 / hi, 2), "max_fps": round(1 / lo, 2)}


def levels(ctx: Ctx, path, dur: float, label: str) -> dict:
    text = ff.run_ff(ctx, ["-i", path, "-map", "0:a:0", "-vn", "-af",
                           "astats=metadata=0:measure_perchannel=none:measure_overall=Peak_level+RMS_level+Noise_floor+Peak_count,ebur128=peak=true",
                           "-f", "null", "-"], duration=dur, msg=f"Measuring sound levels ({label})…",
                     loglevel="info", want_stderr=True)

    def num(pat):
        m = re.findall(pat, text)
        try:
            v = float(m[-1]) if m else None
        except ValueError:
            return None
        return v if v is None or math.isfinite(v) else None  # silence measures -inf dB: "no value"
    return {"lufs": num(r"I:\s+(-?[\d.]+|-inf) LUFS"), "true_peak": num(r"Peak:\s+(-?[\d.]+|-inf) dBFS"),
            "peak": num(r"Peak level dB:\s*(-?[\d.]+|-inf)"), "rms": num(r"RMS level dB:\s*(-?[\d.]+|-inf)"),
            "noise_floor": num(r"Noise floor dB:\s*(-?[\d.]+|-inf)"), "peak_count": num(r"Peak count:\s*([\d.]+)")}


def mmss(t: float) -> str:
    t = int(round(abs(t)))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def describe(path: Path, info: dict, vf: dict | None) -> tuple[dict, list]:
    v, a = stream(info, "video"), stream(info, "audio")
    d = {"name": path.name, "path": str(path), "size_gb": round(path.stat().st_size / 2**30, 2),
         "duration": duration(info), "container": (info.get("format") or {}).get("format_long_name")}
    notes = []
    if v:
        rot = rotation(v)
        w, h = v.get("width"), v.get("height")
        if abs(rot) % 180 == 90:
            w, h = h, w
        bits = 10 if "10" in (v.get("pix_fmt") or "") else 8
        d["video"] = {"codec": v.get("codec_name"), "profile": v.get("profile"), "width": w, "height": h,
                      "rotation": rot, "fps": fps(v), "bit_depth": bits, "hdr": is_hdr(v),
                      "pix_fmt": v.get("pix_fmt"), **(vf or {})}
        shape = "vertical" if h > w else "horizontal" if w > h else "square"
        notes.append(("ok", f"Video: {str(v.get('codec_name', '')).upper()} {bits}-bit, {w}×{h} ({shape}), "
                            f"{fps(v)} fps, length {mmss(d['duration'])}"))
        if is_hdr(v):
            notes.append(("info", f"HDR video ({is_hdr(v)}). The engine converts the colours for normal screens "
                                  "automatically (YouTube/Instagram show SDR best)."))
        if vf and vf.get("vfr"):
            notes.append(("info", f"Variable frame rate ({vf.get('min_fps')}–{vf.get('max_fps')} fps) — normal for "
                                  f"phones. Everything the engine makes uses a steady {fps(v)} fps, which keeps lips in sync."))
        if rot:
            notes.append(("info", f"The phone stored this video rotated by {rot}°; it is turned upright automatically."))
    if a:
        d["audio"] = {"codec": a.get("codec_name"), "rate": int(a.get("sample_rate") or 0),
                      "channels": a.get("channels")}
        if not v:
            notes.append(("ok", f"Audio: {str(a.get('codec_name', '')).upper()}, {int(a.get('sample_rate') or 0) // 1000} kHz, "
                                f"{a.get('channels')} channel(s), length {mmss(d['duration'])}"))
        if int(a.get("sample_rate") or 48000) < 44100:
            notes.append(("warn", f"The sound was recorded at only {int(a['sample_rate']) // 1000} kHz — it will sound "
                                  "less crisp. Record at 48 kHz next time if the device allows."))
    elif v:
        notes.append(("warn", "This video has no sound track, so automatic sync is not possible."))
    return d, notes


def level_notes(lv: dict, label: str) -> list:
    n = []
    if lv.get("peak") is not None and lv["peak"] > -0.2 and (lv.get("peak_count") or 0) > 20:
        n.append(("warn", f"{label}: the sound touches the maximum level in places (clipping) — loud words may "
                          "sound distorted. Cleaning cannot fully repair this; next time set the recorder a little lower."))
    if lv.get("lufs") is not None:
        if lv["lufs"] < -30:
            n.append(("info", f"{label}: quiet recording ({lv['lufs']:.0f} LUFS). It will be raised to a normal level."))
        else:
            n.append(("ok", f"{label}: loudness {lv['lufs']:.0f} LUFS, peak {lv.get('true_peak') or 0:.1f} dB."))
    if lv.get("noise_floor") is not None and lv["noise_floor"] > -55:
        n.append(("info", f"{label}: background noise is noticeable (noise floor {lv['noise_floor']:.0f} dB) — "
                          "voice isolation will help."))
    return n


def job_report(ctx: Ctx, p: dict) -> dict:
    video = _need(p.get("video"), "video")
    audio = _need(p["audio"], "separate audio") if p.get("audio") else None
    ctx.progress(0.01, "Reading file information…")
    vinfo = ff.probe(video)
    vdesc, notes = describe(video, vinfo, vfr_check(video, stream(vinfo, "video") or {}) if stream(vinfo, "video") else None)
    rep = {"video": vdesc}
    total = vdesc["duration"] * (2 if audio else 1) or 1
    if audio:
        ainfo = ff.probe(audio)
        adesc, an = describe(audio, ainfo, None)
        rep["audio"] = adesc
        notes += [(lvl, "Separate audio — " + t[len("Audio: "):] if t.startswith("Audio: ") else t) for lvl, t in an]
        diff = adesc["duration"] - vdesc["duration"]
        if abs(diff) > 2:
            notes.append(("info", f"The separate audio is {mmss(diff)} {'longer' if diff > 0 else 'shorter'} than the "
                                  "video. That is fine: sync finds where they line up. Parts of the video without "
                                  "recorder sound get quiet room tone instead of silence."))
    if stream(vinfo, "audio"):
        ctx.stage(0.05, 0.05 + 0.9 * vdesc["duration"] / total)
        lv = levels(ctx, video, vdesc["duration"], "video sound")
        rep["video"]["levels"] = lv
        if not audio:
            notes += level_notes(lv, "Video sound")
    if audio:
        ctx.stage(0.05 + 0.9 * vdesc["duration"] / total, 0.98)
        lv = levels(ctx, audio, rep["audio"]["duration"], "separate audio")
        rep["audio"]["levels"] = lv
        notes += level_notes(lv, "Separate audio")
    free = hardware.free_gb(video.parent)
    need = 6 + vdesc["duration"] / 3600 * (8 if not p.get("big_output") else 20)
    notes.append(("ok" if free >= need else "warn",
                  f"Free space on that drive: {free:.0f} GB" + (" (enough)." if free >= need else
                                                                 f" — about {need:.0f} GB is needed for this project. Free some space first.")))
    rep["notes"] = [{"level": l, "text": t} for l, t in notes]
    ok = sum(1 for n in rep["notes"] if n["level"] == "warn")
    rep["message"] = "File check done — " + ("no problems found." if not ok else f"{ok} thing(s) to look at.")
    (work_dir(str(video)) / "media_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return rep


def tonemap_chain(v: dict | None) -> str:
    if is_hdr(v):
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv")
    return ""


def scale_short_side(n: int) -> str:
    return f"scale=w='if(gt(iw,ih),-2,{n})':h='if(gt(iw,ih),{n},-2)'"


DECODERS = {"win": [["-hwaccel", "qsv"], ["-hwaccel", "d3d11va"], ["-hwaccel", "dxva2"], []],
            "mac": [["-hwaccel", "videotoolbox"], []], "other": [["-hwaccel", "auto"], []]}


def _input_of(args: list) -> str | None:
    return str(args[args.index("-i") + 1]) if "-i" in args else None


def best_decoder(ctx: Ctx, video: str) -> list[str]:
    """Time each way of decoding THIS kind of video (graphics chip or processor) on a few seconds of it and
    use the fastest. A laptop with two graphics chips can otherwise pick one that is many times slower.
    The answer is remembered per kind of video (codec, size, bit depth)."""
    try:
        info = ff.probe(video)
        v, dur = stream(info, "video"), duration(info)
    except Exception:
        return ["-hwaccel", "auto"]
    if not v:
        return []
    kind = f"{v.get('codec_name')}-{v.get('width')}x{v.get('height')}-{v.get('pix_fmt')}"
    st = config.load_settings()
    known = (st.get("decoders") or {}).get(kind)
    if known is not None:
        return list(known)
    import time
    start = max(0.0, min(dur * 0.1, dur - 4))
    probe = lambda hw: [ff.tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *hw, "-ss", f"{start:.2f}",
                        "-t", "3", "-i", video, "-map", "0:v:0", "-vf", "scale=-2:360", "-f", "null", "-"]
    flags = 0x08000000 if config.IS_WIN else 0
    subprocess.run([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{start:.2f}", "-t", "3", "-i", video,
                    "-map", "0:v:0", "-c", "copy", "-f", "null", "-"], capture_output=True, creationflags=flags)  # read it once
    os_key = "win" if config.IS_WIN else "mac" if config.IS_MAC else "other"
    times, report = {}, []
    ctx.progress(0.0, "Finding the fastest way to read this video on this computer (once, under a minute)…")
    for hw in DECODERS[os_key]:
        ctx.check()
        t0 = time.time()
        try:
            r = subprocess.run(probe(hw), capture_output=True, text=True, timeout=45, creationflags=flags)
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        took = time.time() - t0
        report.append(f"{' '.join(hw) or 'processor'}: {f'{took:.1f} s' if ok else 'not available'}")
        if ok:
            times[tuple(hw)] = took
    cpu = times.pop((), None)
    hw_best = min(times, key=times.get) if times else None
    # a graphics chip must be clearly faster than the processor (the processor never surprises us)
    best = list(hw_best) if hw_best and (cpu is None or times[hw_best] < cpu * 0.85) else []
    ctx.log("Decoder test on 3 s of video: " + "; ".join(report) + f" -> using {' '.join(best) or 'processor'}")
    _remember_decoder(video, best)
    return list(best)


def run_video(ctx: Ctx, p: dict, args: list[str], **kw):
    """Decode with the fastest method for this video (measured once); if it fails, retry on the processor."""
    if p.get("no_hwdec"):
        return ff.run_ff(ctx, args, **kw)
    src = _input_of(args)
    hw = best_decoder(ctx, src) if src else ["-hwaccel", "auto"]
    if not hw:
        return ff.run_ff(ctx, args, **kw)
    try:
        return ff.run_ff(ctx, hw + args, **kw)
    except JobError as e:
        if "disk is full" in str(e):
            raise
        ctx.log("Hardware decoding failed, retrying on the processor: " + str(e))
        _remember_decoder(src, [])  # don't try it again for this kind of video
        return ff.run_ff(ctx, args, **kw)


def _remember_decoder(video: str, hw: list[str]):
    try:
        v = stream(ff.probe(video), "video")
        st = config.load_settings()
        st.setdefault("decoders", {})[f"{v.get('codec_name')}-{v.get('width')}x{v.get('height')}-{v.get('pix_fmt')}"] = hw
        config.save_settings(st)
    except Exception:
        pass


def proxy_fps(v: dict | None) -> str:
    """The preview copy plays at most 30 frames a second (plenty for editing, half the work for 60 fps phones).
    The final video always uses the original frame rate."""
    f = fps(v)
    if f <= 31:
        return output_fps(v)
    return {"60000/1001": "30000/1001", "60": "30", "50": "25"}.get(output_fps(v), "30")


def job_prepare(ctx: Ctx, p: dict) -> dict:
    video = _need(p.get("video"), "video")
    audio = _need(p["audio"], "separate audio") if p.get("audio") else None
    wd = work_dir(str(video))
    vinfo = ff.probe(video)
    v = stream(vinfo, "video")
    dur = duration(vinfo)
    (wd / "video_info.json").write_text(json.dumps(vinfo), encoding="utf-8")
    hardware.disk_check(wd, 0.5 + dur / 3600 * 1.0, "the preview copy and sound files")
    out_v, out_p = wd / "video_16k.wav", wd / "proxy.mp4"
    key = key_of({"v": file_sig(video), "ver": 3})
    if not (step_done(out_v, key) and (not v or step_done(out_p, key))):
        tv, tp = ff.tmp_of(out_v), ff.tmp_of(out_p)
        args = ["-i", video]
        if stream(vinfo, "audio"):
            args += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tv]
        if v:
            vf = ",".join(x for x in [scale_short_side(540), tonemap_chain(v), "format=yuv420p"] if x)
            args += ["-map", "0:v:0"] + (["-map", "0:a:0"] if stream(vinfo, "audio") else []) + [
                "-vf", vf, "-fps_mode", "cfr", "-r", proxy_fps(v), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "28", "-g", "15", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tp]
        ctx.stage(0.0, 0.85 if audio else 1.0)
        run_video(ctx, p, args, duration=dur, msg="Making the preview copy and reading the sound…")
        if stream(vinfo, "audio"):
            ff.finish(tv, out_v)
            mark_done(out_v, key)
        if v:
            ff.finish(tp, out_p)
            mark_done(out_p, key)
    res = {"work_dir": str(wd), "video_wav": str(out_v) if out_v.exists() else None,
           "proxy": str(out_p) if out_p.exists() else None, "info": str(wd / "video_info.json"), "duration": dur}
    if audio:
        out_a = wd / "audio_16k.wav"
        ainfo = ff.probe(audio)
        akey = key_of({"a": file_sig(audio), "ver": 1})
        if not step_done(out_a, akey):
            ctx.stage(0.85, 1.0)
            ta = ff.tmp_of(out_a)
            ff.run_ff(ctx, ["-i", audio, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", ta],
                      duration=duration(ainfo), msg="Reading the separate audio…")
            ff.finish(ta, out_a)
            mark_done(out_a, akey)
        res["audio_wav"] = str(out_a)
    res["message"] = "Prepared: preview copy and sound files are ready."
    return res
