"""Automatic framing: a calm, camera-operator-like path that keeps the speaker well placed.

- The face position (5 per second) is cleaned up (gaps filled, brief false detections ignored).
- The "camera" holds still while the face stays inside a calm zone, and glides (eased, speed-limited)
  only when the face really moves — like a person operating the camera, never jittery.
- At every cut the framing alternates between normal and a closer "punch-in" (about 118%), which hides
  jump cuts in a talk.
All positions are fractions of the picture (0..1), so they apply to any resolution.
"""
from fractions import Fraction

import numpy as np

LEAD = 0.40  # face centre sits 40% down from the top of the frame (room above, body below)
CALM = 0.06  # calm zone: the camera only moves if the face moves more than 6% of the picture height
MAX_SPEED = 0.35  # never move more than 35% of the picture per second (about 1.2% per frame; limit is 3%)


def face_series(faces: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Times and face centre x, y and height (0..1); gaps up to 3 s are bridged, longer ones hold."""
    s = faces.get("samples") or []
    t = np.array([x["t"] for x in s], float)
    cx = np.full(len(s), np.nan)
    cy = np.full(len(s), np.nan)
    fh = np.full(len(s), np.nan)
    for i, x in enumerate(s):
        f = x.get("face")
        if f and f[4] >= 0.6:
            cx[i], cy[i], fh[i] = f[0] + f[2] / 2, f[1] + f[3] / 2, f[3]
    if not len(t):
        return np.array([0.0]), np.array([0.5]), np.array([0.35]), np.array([0.15])
    ok = ~np.isnan(cx)
    if not ok.any():
        return t, np.full(len(t), 0.5), np.full(len(t), 0.35), np.full(len(t), 0.15)
    out = []
    for arr in (cx, cy, fh):
        a = np.interp(t, t[ok], arr[ok])  # bridge gaps (and hold at the ends)
        out.append(a)
    return t, out[0], out[1], out[2]


def _median(a: np.ndarray, n: int) -> np.ndarray:
    if n < 3 or len(a) < n:
        return a.copy()
    pad = n // 2
    p = np.pad(a, pad, mode="edge")
    return np.array([np.median(p[i:i + n]) for i in range(len(a))])


def calm_path(t: np.ndarray, target: np.ndarray, fps: float, calm: float = CALM) -> np.ndarray:
    """Like an editor setting keyframes (the whole recording is known in advance, so there is no lag):
    1. find the stretches where the speaker stands still (within the calm zone) for at least a second;
    2. frame each of them exactly (steady, no jitter);
    3. glide between them with an eased move that starts when the speaker starts moving,
       never faster than MAX_SPEED (so no jumps)."""
    n = len(target)
    tgt = _median(target, 3)  # drop single false detections
    runs, i = [], 0
    while i < n:  # greedy stable runs: every value within calm/2 of the run's median
        j = i + 1
        while j < n and abs(tgt[j] - np.median(tgt[i:j + 1])) <= calm / 2:
            j += 1
        runs.append((i, j))
        i = j
    stable = [(a, b, float(np.median(tgt[a:b]))) for a, b in runs if (b - a) / fps >= 1.0]
    if not stable:  # always moving: smooth heavily and limit the speed
        k = max(1, int(fps * 0.8))
        ker = np.hanning(2 * k + 1)
        ker /= ker.sum()
        return _limit(np.convolve(np.pad(tgt, k, mode="edge"), ker, mode="valid"), fps)
    out = np.empty(n)
    out[:stable[0][0]] = stable[0][2]
    for idx, (a, b, lv) in enumerate(stable):
        out[a:b] = lv
        if idx + 1 < len(stable):
            na, nb, nlv = stable[idx + 1]
            gap_lo, gap_hi = b, na  # samples where the speaker is moving
            need = abs(nlv - lv) / MAX_SPEED * fps  # samples needed at the speed limit
            mid = (gap_lo + gap_hi) / 2
            half = max((gap_hi - gap_lo) / 2, need / 2, 0.3 * fps)
            lo, hi = int(np.floor(mid - half)), int(np.ceil(mid + half))
            lo, hi = max(a + 1, lo), min(nb - 1, hi)
            out[b:na] = lv
            if hi > lo:
                x = (np.arange(lo, hi) - lo) / (hi - lo)
                out[lo:hi] = lv + (nlv - lv) * (0.5 - 0.5 * np.cos(np.pi * x))
                out[hi:na] = nlv
    out[stable[-1][1]:] = stable[-1][2]
    return _limit(out, fps)


def _limit(path: np.ndarray, fps: float) -> np.ndarray:
    """Hard speed limit (per sample), keeping the path continuous."""
    step = MAX_SPEED / fps
    out = path.copy()
    for i in range(1, len(out)):
        out[i] = out[i - 1] + np.clip(out[i] - out[i - 1], -step, step)
    return out


class Framer:
    """Gives the crop rectangle (in pixels of the full-size video) for any moment."""

    def __init__(self, faces: dict | None, W: int, H: int, aspect: float):
        self.W, self.H, self.aspect = W, H, aspect
        if faces:
            t, cx, cy, _ = face_series(faces)
            fps = float(faces.get("fps") or 5)
        else:
            t, cx, cy, fps = np.array([0.0]), np.array([0.5]), np.array([0.35]), 5.0
        self.t = t
        self.px = calm_path(t, cx, fps) if len(t) > 1 else cx
        self.py = calm_path(t, cy, fps) if len(t) > 1 else cy
        if W / H > aspect:  # the biggest window of the wanted shape inside the picture
            self.base_h, self.base_w = H, H * aspect
        else:
            self.base_w, self.base_h = W, W / aspect

    def at(self, times: np.ndarray, zoom: float = 1.0, fixed_y: float | None = None,
           lead: float = LEAD) -> tuple[int, int, np.ndarray, np.ndarray]:
        cw = int(round(self.base_w / zoom / 2)) * 2
        ch = int(round(self.base_h / zoom / 2)) * 2
        fx = np.interp(times, self.t, self.px)
        fy = np.full(len(times), fixed_y) if fixed_y is not None else np.interp(times, self.t, self.py)
        x = np.clip(fx * self.W - cw / 2, 0, self.W - cw)
        y = np.clip(fy * self.H - lead * ch, 0, self.H - ch)
        return cw, ch, np.round(x).astype(int), np.round(y).astype(int)


def part_zooms(n_parts: int, punch: float, overrides: list, segs_s: list) -> list[float]:
    """Alternate normal / punch-in at every cut; a manual setting for a section wins."""
    out = []
    for k in range(n_parts):
        z = 1.0 if (k % 2 == 0 or punch <= 1.0) else punch
        a, b = segs_s[k]
        mid = (a + b) / 2
        for o in overrides or []:
            if float(o.get("start", 0)) <= mid < float(o.get("end", 0)) and o.get("zoom"):
                z = float(o["zoom"])
        out.append(z)
    return out


def override_y(overrides: list, a: float, b: float, framer: "Framer | None" = None) -> float | None:
    """A fixed face height for this section: given directly ('y'), or 'hold' = the camera stays still at the
    speaker's typical position in that section."""
    mid = (a + b) / 2
    for o in overrides or []:
        if float(o.get("start", 0)) <= mid < float(o.get("end", 0)):
            if o.get("y") is not None:
                return float(o["y"])
            if o.get("hold") and framer is not None:
                m = (framer.t >= float(o["start"])) & (framer.t <= float(o["end"]))
                return float(np.median(framer.py[m])) if m.any() else None
    return None


def sendcmd_text(fr: Fraction, x: np.ndarray, y: np.ndarray, name: str = "crop@fr", w: np.ndarray | None = None,
                 h: np.ndarray | None = None) -> str:
    """ffmpeg 'sendcmd' lines that move (and, for a slow zoom, resize) the crop window frame by frame."""
    lines, last = [], None
    for i in range(len(x)):
        cur = (x[i], y[i], None if w is None else w[i], None if h is None else h[i])
        if cur != last:
            t = float(Fraction(i) / fr)
            size = f"{name} w {int(w[i])}, {name} h {int(h[i])}, " if w is not None else ""
            lines.append(f"{t:.4f} {size}{name} x {int(x[i])}, {name} y {int(y[i])};")
            last = cur
    return "\n".join(lines) + "\n"
