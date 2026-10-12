"""Stage 3 picture: framing (16:9 that follows the face, or blurred sides), punch-ins at cuts, colour,
and cover ranges (picture replaced by other moments of the recording or a title card, sound continues).

The finished video is planned as a list of PIECES on the output timeline. Every piece is encoded on its
own (so an interrupted render continues where it stopped) and they are joined without re-encoding.
Every piece has an exact number of frames, so the sound - assembled separately - stays in sync.
"""
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

DISSOLVE_S = 0.5
CUT_DISSOLVE_S = 0.4  # "smooth dissolve at cuts": length of the mix between the two sides of a cut


@dataclass
class Piece:
    kind: str            # main | cover | in | out   (in: main -> cover dissolve, out: cover -> main)
    n: int               # frames
    part: int = 0        # which kept part (for punch-in / colour)
    src: int = 0         # first source frame (main picture)
    cov: int = -1        # which cover range
    coff: int = 0        # first frame inside that cover's clip
    extra: dict = field(default_factory=dict)


def output_ranges_idx(keep_frames: list, covers_src: list, fr: Fraction) -> list[tuple[int, int, int]]:
    """Cover ranges given in source seconds -> (first, last, which cover) frame ranges on the edited (output)
    timeline, in time order. Overlapping covers are joined (the earlier one's settings are used)."""
    out = []
    for idx, c in enumerate(covers_src):
        a, b = int(round(float(c["start"]) * fr)), int(round(float(c["end"]) * fr))
        o = 0
        lo = hi = None
        for s, e in keep_frames:
            x, y = max(s, a), min(e, b)
            if y > x:
                lo = o + (x - s) if lo is None else lo
                hi = o + (y - s)
            o += e - s
        if lo is not None and hi - lo >= 2:
            out.append((lo, hi, idx))
    out.sort()
    merged = []
    for r in out:
        if merged and r[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], r[1]), merged[-1][2])
        else:
            merged.append(r)
    return merged


def output_ranges(keep_frames: list, covers_src: list, fr: Fraction) -> list[tuple[int, int]]:
    return [(a, b) for a, b, _ in output_ranges_idx(keep_frames, covers_src, fr)]


def plan(keep_frames: list, cover_out: list, fr: Fraction, hard: frozenset = frozenset(), xcuts: bool = False,
         src_frames: int | None = None) -> list[Piece]:
    """Split the edited timeline into pieces. Dissolves of DISSOLVE_S at cover edges (when the cover is long
    enough). A dissolve split by a cut keeps its progress (extra['tpos'] = frames already dissolved)."""
    D = int(round(DISSOLVE_S * fr))
    total = sum(e - s for s, e in keep_frames)
    part_start = [0]
    for s, e in keep_frames:
        part_start.append(part_start[-1] + e - s)
    trans = []  # (lo, hi, kind, cover)
    for ci, (a, b) in enumerate(cover_out):
        if b - a < 2 * D or ci in hard:
            continue  # too short for dissolves (or a stretch with no camera picture): hard cuts
        if a > 0:
            trans.append((max(0, a - D // 2), a + (D - D // 2), "in", ci))
        if b < total:
            trans.append((b - D // 2, min(total, b + (D - D // 2)), "out", ci))
    if xcuts:  # a short dissolve centred on every cut (uses the picture just past each side of the cut)
        X = int(round(CUT_DISSOLVE_S * fr))
        for k in range(1, len(keep_frames)):
            P = part_start[k]
            lo, hi = P - X // 2, P + (X - X // 2)
            ka, kb = keep_frames[k - 1], keep_frames[k]
            enough = (ka[1] - ka[0] >= 2 * X and kb[1] - kb[0] >= 2 * X and kb[0] - X // 2 >= 0
                      and (src_frames is None or ka[1] + X <= src_frames) and ka[0] >= 0)
            if enough and not any(not (hi <= a or lo >= b) for a, b in cover_out) \
                    and not any(not (hi <= t[0] or lo >= t[1]) for t in trans):
                trans.append((lo, hi, "x", k))
    bounds = set(part_start) | {0, total}
    for lo, hi, _, _ in trans:
        bounds.update((lo, hi))
    for a, b in cover_out:
        bounds.update((a, b))
    cuts = sorted(x for x in bounds if 0 <= x <= total)

    def clip_start(ci):
        a, b = cover_out[ci]
        has_in = any(t[3] == ci and t[2] == "in" for t in trans)
        return a - D // 2 if has_in else a

    pieces: list[Piece] = []
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        if hi <= lo:
            continue
        k = max(i for i in range(len(keep_frames)) if part_start[i] <= lo)
        src = keep_frames[k][0] + (lo - part_start[k])
        t = next((x for x in trans if x[0] <= lo and hi <= x[1]), None)
        inside = next((ci for ci, (a, b) in enumerate(cover_out) if a <= lo and hi <= b), -1)
        if t and t[2] == "x":  # both sides of a cut, mixed
            kb = t[3]
            ka = kb - 1
            pieces.append(Piece("xcut", hi - lo, ka, keep_frames[ka][0] + (lo - part_start[ka]), -1, 0,
                                {"tpos": lo - t[0], "tlen": t[1] - t[0], "part2": kb,
                                 "src2": keep_frames[kb][0] + (lo - part_start[kb])}))
        elif t:
            ci = t[3]
            pieces.append(Piece(t[2], hi - lo, k, src, ci, lo - clip_start(ci),
                                {"tpos": lo - t[0], "tlen": t[1] - t[0]}))
        elif inside >= 0:
            pieces.append(Piece("cover", hi - lo, k, src, inside, lo - clip_start(inside)))
        else:
            pieces.append(Piece("main", hi - lo, k, src))
    merged: list[Piece] = []  # join neighbours of the same kind (fewer files)
    for p in pieces:
        q = merged[-1] if merged else None
        if q and p.kind == q.kind == "main" and p.part == q.part and q.src + q.n == p.src:
            q.n += p.n
        elif q and p.kind == q.kind == "cover" and p.cov == q.cov and q.coff + q.n == p.coff:
            q.n += p.n
        elif (q and p.kind == q.kind and p.kind in ("in", "out") and p.cov == q.cov and p.part == q.part
              and q.src + q.n == p.src and q.coff + q.n == p.coff):
            q.n += p.n
        elif (q and p.kind == q.kind == "xcut" and p.part == q.part and q.src + q.n == p.src
              and q.extra["src2"] + q.n == p.extra["src2"]):
            q.n += p.n
        else:
            merged.append(p)
    return merged


def cover_clip_frames(cover_out: list, ci: int, fr: Fraction, total: int, hard: frozenset = frozenset()) -> int:
    """Length of the clip made for cover ci (its range plus half a dissolve on each side)."""
    D = int(round(DISSOLVE_S * fr))
    a, b = cover_out[ci]
    if b - a < 2 * D or ci in hard:
        return b - a
    return (b - a) + (D // 2 if a > 0 else 0) + ((D - D // 2) if b < total else 0)


def listening_clips(analysis: dict | None, faces: dict | None, avoid: list, min_s: float = 1.2) -> list[list[float]]:
    """Moments where the speaker is silent and their face is visible (good to cover other parts)."""
    if not analysis:
        return []
    sp = analysis.get("speech") or []
    gaps = [[sp[k - 1][1], sp[k][0]] for k in range(1, len(sp))]
    out = []
    face_t = np.array([s["t"] for s in (faces or {}).get("samples", [])])
    face_ok = np.array([bool(s.get("face")) for s in (faces or {}).get("samples", [])])
    for a, b in gaps:
        a, b = a + 0.15, b - 0.15
        if b - a < min_s:
            continue
        if any(not (b <= x0 or a >= x1) for x0, x1 in avoid):
            continue
        if len(face_t):
            m = (face_t >= a) & (face_t <= b)
            if m.any() and face_ok[m].mean() < 0.8:
                continue
        out.append([round(a, 2), round(min(b, a + 4.0), 2)])
    out.sort(key=lambda c: -(c[1] - c[0]))
    return out


def colour_gains(faces: dict | None, a_s: float, b_s: float) -> tuple[float, float, float] | None:
    """Gentle automatic match: bring this part's average colour towards the whole video's typical colour."""
    if not faces or not faces.get("samples"):
        return None
    s = faces["samples"]
    all_rgb = np.array([x["rgb"] for x in s], float)
    ref = np.median(all_rgb, axis=0)
    t = np.array([x["t"] for x in s])
    m = (t >= a_s) & (t <= b_s)
    if m.sum() < 3:
        return None
    cur = all_rgb[m].mean(axis=0)
    g = np.clip(ref / np.maximum(cur, 1.0), 0.9, 1.1)
    if np.all(np.abs(g - 1) < 0.015):
        return None
    return tuple(round(float(x), 3) for x in g)


def _wheel(c: dict, wheel: str, ch: str) -> float:
    return max(-1.0, min(1.0, float(((c.get(wheel) or {}).get(ch)) or 0)))


def colour_filter(st: dict, gains) -> str:
    c = st.get("colour") or {}
    f = []
    if gains and c.get("auto", True):
        f.append(f"colorchannelmixer=rr={gains[0]}:gg={gains[1]}:bb={gains[2]}")
    br, ct, sa = float(c.get("brightness", 0)), float(c.get("contrast", 1)), float(c.get("saturation", 1))
    if abs(br) > 1e-3 or abs(ct - 1) > 1e-3 or abs(sa - 1) > 1e-3:
        f.append(f"eq=brightness={br:.3f}:contrast={ct:.3f}:saturation={sa:.3f}")
    wm = float(c.get("warmth", 0))
    if abs(wm) > 1e-3:
        f.append(f"colortemperature=temperature={int(6500 - 1000 * wm)}")
    # colour wheels, as in Resolve: per channel  out = (gain * x + lift * (1 - x)) ^ (1 / gamma)
    # (lift moves the shadows, gamma the midtones, gain the highlights; the wheels give a push per channel)
    chans = {}
    for ch in "rgb":
        lift = 0.25 * _wheel(c, "lift", ch)
        gain = 0.5 * _wheel(c, "gain", ch)
        gam = 1 + 0.5 * _wheel(c, "gamma", ch)
        if abs(lift) > 1e-3 or abs(gain) > 1e-3 or abs(gam - 1) > 1e-3:
            chans[ch] = (f"'clip(maxval*pow(clip({1 + gain:.4f}*val/maxval+{lift:.4f}*(1-val/maxval),0,1),"
                         f"{1 / max(0.2, gam):.4f}),0,maxval)'")
    if chans:
        f.append("lutrgb=" + ":".join(f"{ch}={e}" for ch, e in chans.items()))
    return ",".join(f)
