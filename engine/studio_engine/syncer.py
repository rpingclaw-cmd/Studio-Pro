"""Find how the separate recorder lines up with the camera: offset and clock drift.

audio_time = a + b * video_time   (same convention as the browser version)

1. Coarse: correlate the loudness "onsets" of both whole recordings (100 per second).
2. Fine: at 3 places (start / middle / end) correlate the real waveforms (GCC-PHAT, sub-sample peak).
3. Fit offset + drift through the fine points; the leftover error is reported so you can trust it.
"""
import numpy as np

from .jobs import Cancelled, Ctx, JobError
from .wavio import open_pcm16


def _mono(path):
    arr, rate, ch = open_pcm16(path)
    return arr, rate, ch


def envelope(path, hop_s: float = 0.01) -> np.ndarray:
    arr, rate, ch = _mono(path)
    hop = int(rate * hop_s)
    n = arr.shape[0] // hop
    e = np.empty(n, np.float32)
    step = 6000  # frames of envelope per chunk (~1 minute) to keep memory small
    for i in range(0, n, step):
        j = min(n, i + step)
        x = np.asarray(arr[i * hop:j * hop], dtype=np.float32).mean(axis=1) / 32768.0
        e[i:j] = np.log(1e-6 + np.sqrt((x.reshape(j - i, hop) ** 2).mean(axis=1)))
    w = 50
    c = np.cumsum(np.concatenate([[0.0], e.astype(np.float64)]))
    idx = np.arange(n)
    lo = np.maximum(0, idx - w + 1)
    mean = (c[idx + 1] - c[lo]) / (idx + 1 - lo)
    return np.maximum(0.0, e - mean).astype(np.float32)


def xcorr(a: np.ndarray, b: np.ndarray, kmin: int, kmax: int, phat: float = 0.0) -> tuple[float, float]:
    """r[k] = sum a[n] * b[n+k]; best k in [kmin, kmax] with sub-sample refinement. Returns (lag, confidence)."""
    a = a - a.mean()
    b = b - b.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b) + 1)))
    A, B = np.fft.rfft(a, n), np.fft.rfft(b, n)
    R = np.conj(A) * B
    if phat:
        R = R / (np.abs(R) ** phat + 1e-12)
    r = np.fft.irfft(R, n)
    ks = np.arange(kmin, kmax + 1)
    vals = r[ks % n]
    i = int(np.argmax(vals))
    best = vals[i]
    conf = float((best - vals.mean()) / (vals.std() + 1e-12))
    if 0 < i < len(vals) - 1:
        y0, y1, y2 = vals[i - 1], vals[i], vals[i + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
    else:
        frac = 0.0
    return float(ks[i] + max(-1.0, min(1.0, frac))), conf


def read_window(path, start_s: float, dur_s: float) -> tuple[np.ndarray, int, float]:
    arr, rate, ch = _mono(path)
    a = int(max(0.0, start_s) * rate)
    b = min(arr.shape[0], a + int(dur_s * rate))
    x = np.asarray(arr[a:b], dtype=np.float32).mean(axis=1) / 32768.0
    return x, rate, a / rate


def fine_offset(vpath, apath, tv: float, a0: float, win: float = 20.0, search: float = 3.0) -> dict:
    xv, rate, sv = read_window(vpath, tv - win / 2, win)
    xa, rate_a, sa = read_window(apath, sv + a0 - search, win + 2 * search)
    if rate != rate_a:
        raise JobError("The two sound files have different sample rates — run Prepare again.")
    if len(xv) < rate * 2 or len(xa) < rate * 2:
        return {"tv": sv, "off": a0, "conf": 0.0}
    lag, conf = xcorr(xv, xa, -int(search * rate), int(search * rate) + int(len(xa) - len(xv)), phat=0.8)
    return {"tv": sv, "off": sa + lag / rate - sv, "conf": conf}


def compute(ctx: Ctx | None, vpath, apath) -> dict:
    say = (lambda f, m: ctx.progress(f, m)) if ctx else (lambda f, m: None)
    say(0.05, "Comparing the loudness pattern of both recordings…")
    ev, ea = envelope(vpath), envelope(apath)
    if ctx:
        ctx.check()
    lag, cconf = xcorr(ev, ea, -(len(ev) - 1), len(ea) - 1)
    a0 = lag / 100.0
    _, rate, _ = _mono(vpath)
    dur_v = _mono(vpath)[0].shape[0] / rate
    dur_a = _mono(apath)[0].shape[0] / _mono(apath)[1]
    lo, hi = max(0.0, -a0) + 12, min(dur_v, dur_a - a0) - 12
    if hi - lo < 5:
        raise JobError("The recordings overlap for less than half a minute — are these the right two files?")
    pts = []
    for k, f in enumerate((0.1, 0.5, 0.9)):
        tv = lo + (hi - lo) * f
        say(0.3 + 0.2 * k, f"Fine-tuning at {int(tv // 60)}:{int(tv % 60):02d}…")
        if ctx:
            ctx.check()
        pts.append(fine_offset(vpath, apath, tv, a0))
    good = [p for p in pts if p["conf"] >= 4] or sorted(pts, key=lambda p: -p["conf"])[:1]
    if len(good) >= 2:
        tvs = np.array([p["tv"] for p in good])
        offs = np.array([p["off"] for p in good])
        slope, icpt = np.polyfit(tvs, offs, 1)
        if abs(slope) > 0.0008:  # 800 ppm is far more than any real clock drift: distrust it
            slope, icpt = 0.0, float(np.median(offs))
    else:
        slope, icpt = 0.0, good[0]["off"]
    a, b = float(icpt), float(1.0 + slope)
    resid = [abs(p["off"] - (a + slope * p["tv"])) * 1000 for p in good]
    conf = min([cconf] + [p["conf"] for p in good])
    res = {"a": a, "b": b, "conf": round(conf, 2), "coarse_conf": round(cconf, 2),
           "drift_ppm": round(slope * 1e6, 1), "residual_ms": round(max(resid) if resid else 0.0, 2),
           "points": [{"tv": round(p["tv"], 3), "off": round(p["off"], 5), "conf": round(p["conf"], 1)} for p in pts]}
    conf = float(conf)
    sure = conf >= 4 and res["residual_ms"] < 20
    res["message"] = (f"Sync found: the recorder is {abs(a):.3f} s {'ahead of' if a > 0 else 'behind'} the camera, "
                      f"clock drift {res['drift_ppm']:+.0f} ppm. Check at 3 points: max error {res['residual_ms']:.1f} ms"
                      + (" ✓" if sure else " — ⚠ low confidence: please check lip sync by eye."))
    res["confident"] = bool(sure)
    return res


# ---------- the words: the same sentences must be heard at the same moment on both recordings ----------
WORD_WIN = 40.0


def _tok(w: str) -> str:
    import re
    return re.sub(r"[^\w]", "", w.lower())


def _match_times(wa: list, wb: list) -> list[tuple[float, float]]:
    """Pairs of start times of the same words (in order) in two word lists."""
    import difflib
    ta, tb = [_tok(w["w"]) for w in wa], [_tok(w["w"]) for w in wb]
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    out = []
    for blk in sm.get_matching_blocks():
        if blk.size >= 2:  # runs of 2+ words: single common words ("the") could pair up wrongly
            out += [(wa[blk.a + k]["s"], wb[blk.b + k]["s"]) for k in range(blk.size) if ta[blk.a + k]]
    return out


def _words(ctx, path, start: float, dur: float, quality: str, language):
    from . import transcribe
    x, rate, t0 = read_window(path, max(0.0, start), dur)
    if rate != 16000:
        return []
    w, _ = transcribe.words_of(None, x.astype(np.float32), quality, language, offset_s=t0)
    return w


def word_check(ctx: Ctx | None, vpath, apath, res: dict, quality: str = "fast", language=None) -> dict:
    """Transcribe ~40 s of each recording at 3 points and check the same words land at the same video time."""
    a, b = res["a"], res["b"]
    _, vr, _ = _mono(vpath)
    dur_v = _mono(vpath)[0].shape[0] / vr
    dur_a = _mono(apath)[0].shape[0] / _mono(apath)[1]
    lo, hi = max(0.0, -a / b) + 5, min(dur_v, (dur_a - a) / b) - WORD_WIN - 5
    if hi <= lo:
        return {"checked": 0}
    from . import models
    models.whisper_model(ctx, quality)  # (downloads once, with progress)
    pts = []
    for k, f in enumerate((0.15, 0.5, 0.85)):
        if ctx:
            ctx.check()
            ctx.progress(0.7 + 0.1 * k, f"Checking the words at 3 points ({k + 1} of 3)…")
        tv = lo + (hi - lo) * f
        cam = _words(ctx, vpath, tv, WORD_WIN, quality, language)
        rec = _words(ctx, apath, a + b * tv - 3, WORD_WIN + 6, quality, language)
        for w in rec:
            w["s"] = (w["s"] - a) / b
        pairs = _match_times(cam, rec)
        if len(pairs) >= 4:
            d = np.array([r - c for c, r in pairs])
            pts.append({"tv": round(tv, 1), "words": len(pairs), "of": len(cam), "err_ms": round(float(np.median(d)) * 1000, 1)})
        else:
            pts.append({"tv": round(tv, 1), "words": len(pairs), "of": len(cam), "err_ms": None})
    ok = [p for p in pts if p["err_ms"] is not None and abs(p["err_ms"]) < 120]
    return {"checked": len(pts), "agree": len(ok), "points": pts}


def word_sync(ctx: Ctx | None, vpath, apath, quality: str = "fast", language=None) -> dict | None:
    """Backup when the sound patterns do not match well: find each camera passage's words in the recorder."""
    _, vr, _ = _mono(vpath)
    dur_v = _mono(vpath)[0].shape[0] / vr
    dur_a = _mono(apath)[0].shape[0] / _mono(apath)[1]
    from . import models
    models.whisper_model(ctx, quality)
    if ctx:
        ctx.progress(0.55, "Listening to the words of the whole recorder file (backup sync)…")
    rec = _words(ctx, apath, 0, dur_a, quality, language)
    offs = []
    for f in (0.15, 0.5, 0.85):
        tv = max(0.0, (dur_v - WORD_WIN) * f)
        cam = _words(ctx, vpath, tv, WORD_WIN, quality, language)
        pairs = _match_times(cam, rec)
        if len(pairs) >= 6:
            offs.append((tv + WORD_WIN / 2, float(np.median([r - c for c, r in pairs]))))
    if not offs:
        return None
    if len(offs) >= 2:
        slope, icpt = np.polyfit([t for t, _ in offs], [o for _, o in offs], 1)
        if abs(slope) > 0.0008:
            slope, icpt = 0.0, float(np.median([o for _, o in offs]))
    else:
        slope, icpt = 0.0, offs[0][1]
    return {"a": float(icpt), "b": float(1 + slope)}


def job_sync(ctx: Ctx, p: dict) -> dict:
    from pathlib import Path
    v, a = p.get("video_wav"), p.get("audio_wav")
    if not (v and a and Path(v).is_file() and Path(a).is_file()):
        raise JobError("Run Prepare first (it makes the sound files used for sync).")
    try:
        res = compute(ctx, v, a)
    except JobError:
        res = None
    words_ok = p.get("check_words", True) and _have_whisper()
    if words_ok and (res is None or not res["confident"]):
        ws = word_sync(ctx, v, a, language=p.get("language"))
        if ws:  # fine-tune the word-based answer with the exact sound comparison
            pts = [fine_offset(v, a, tv, ws["a"] + (ws["b"] - 1) * tv, search=1.0)
                   for tv in np.linspace(30, max(31.0, _mono(v)[0].shape[0] / 16000 - 60), 3)]
            good = [q for q in pts if q["conf"] >= 4]
            if good:
                off = np.array([q["off"] for q in good]); tvs = np.array([q["tv"] for q in good])
                slope, icpt = (np.polyfit(tvs, off, 1) if len(good) >= 2 else (0.0, off[0]))
                ws = {"a": float(icpt), "b": float(1 + slope)}
            res = {**ws, "conf": 4.0 if good else 2.0, "coarse_conf": 0.0, "drift_ppm": round((ws["b"] - 1) * 1e6, 1),
                   "residual_ms": 0.0, "points": [], "confident": bool(good), "by_words": True,
                   "message": f"Sync found from the words: the recorder is {abs(ws['a']):.3f} s "
                              f"{'ahead of' if ws['a'] > 0 else 'behind'} the camera"
                              + (" ✓" if good else " — ⚠ please check lip sync by eye.")}
    if res is None:
        raise JobError("Could not find the sync — are these the right two files? (No common sound or words found.)")
    res["video_dur"] = round(_mono(v)[0].shape[0] / _mono(v)[1], 3)
    res["audio_dur"] = round(_mono(a)[0].shape[0] / _mono(a)[1], 3)
    if words_ok:
        try:
            wc = word_check(ctx, v, a, res, language=p.get("language"))
            res["words"] = wc
            if wc.get("checked"):
                res["message"] += (f" Words agree at {wc['agree']} of {wc['checked']} points ✓" if wc["agree"] == wc["checked"]
                                   else f" ⚠ The words agree at only {wc['agree']} of {wc['checked']} points — check lip sync by eye.")
        except Cancelled:
            raise
        except Exception as e:  # the word check is extra: never fail the sync because of it (e.g. no internet for the model)
            res["words"] = {"error": str(e)[:200]}
    return res


def _have_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
