"""Find how the separate recorder lines up with the camera: offset and clock drift.

audio_time = a + b * video_time   (same convention as the browser version)

1. Coarse: correlate the loudness "onsets" of both whole recordings (100 per second).
2. Fine: at 3 places (start / middle / end) correlate the real waveforms (GCC-PHAT, sub-sample peak).
3. Fit offset + drift through the fine points; the leftover error is reported so you can trust it.
"""
import numpy as np

from .jobs import Ctx, JobError
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


def job_sync(ctx: Ctx, p: dict) -> dict:
    from pathlib import Path
    v, a = p.get("video_wav"), p.get("audio_wav")
    if not (v and a and Path(v).is_file() and Path(a).is_file()):
        raise JobError("Run Prepare first (it makes the sound files used for sync).")
    return compute(ctx, v, a)
