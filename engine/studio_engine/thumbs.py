"""Thumbnail suggestions: the best moments for a YouTube thumbnail (face big enough, centred, sharp, well
exposed), spread over the talk, each made as a 1280x720 close-up in the final look."""
from fractions import Fraction

import numpy as np

from . import faces, ff, media
from .jobs import Ctx, JobError, file_sig, key_of


def score(s: dict, sharp_ref: float) -> float:
    f = s.get("face")
    if not f or f[4] < 0.7:
        return 0.0
    size = f[3]  # face height / picture height
    size_s = np.exp(-((np.log(max(size, 1e-3)) - np.log(0.16)) ** 2) / 0.5)  # best around 16%
    cx = f[0] + f[2] / 2
    centre_s = 1 - min(1.0, abs(cx - 0.5) * 2) ** 2
    sharp_s = min(1.5, s["sharp"] / max(sharp_ref, 1e-6))
    expo_s = 1.0 if 70 <= s["luma"] <= 190 else 0.6
    return float(size_s * centre_s * sharp_s * expo_s * f[4])


def pick(samples: list, n: int = 6, min_gap: float = 20.0) -> list[dict]:
    if not samples:
        return []
    ref = float(np.median([s["sharp"] for s in samples])) or 1.0
    ranked = sorted(samples, key=lambda s: -score(s, ref))
    dur = samples[-1]["t"]
    gap = max(3.0, min(min_gap, dur / (n * 2)))
    out = []
    for s in ranked:
        if score(s, ref) <= 0:
            break
        if all(abs(s["t"] - o["t"]) >= gap for o in out):
            out.append(s)
        if len(out) >= n:
            break
    return sorted(out, key=lambda s: s["t"])


def job_thumbs(ctx: Ctx, p: dict) -> dict:
    from .render import Pictures
    video = media._need(p.get("video"), "video")
    fd = faces.load(str(video))
    if not fd:
        raise JobError("Press “Find the speaker” first (step 8) — thumbnails are chosen from where the face is.")
    vinfo = ff.probe(video)
    v = media.stream(vinfo, "video")
    fr = Fraction(media.output_fps(v))
    st = {**(p.get("settings") or {}), "layout": "crop169", "res": "720"}
    chosen = pick(fd["samples"], int(p.get("n", 6)))
    if not chosen:
        raise JobError("No clear view of a face was found for a thumbnail.")
    d = media.work_dir(str(video)) / "thumbs"
    d.mkdir(exist_ok=True)
    outs = []
    for i, s in enumerate(chosen):
        ctx.check()
        ctx.progress(i / len(chosen), f"Making thumbnail suggestion {i + 1} of {len(chosen)}…")
        a = int(round(s["t"] * fr))
        w = int(5 * fr)
        segs = [(max(0, a - 3 * w), max(1, a - w)), (max(0, a - w), a + w)]  # 2nd part = close-up
        pics = Pictures(v, "full", {**st, "punch": 1.3}, 1920, fd, segs, fr)
        pics.g.update({"OW": 1280, "OH": 720})
        vf, _ = pics.vf(a, 1, 1, None)
        out = d / f"thumb_{key_of([s['t'], st, file_sig(video)])}.jpg"
        media.run_video(ctx, p, ["-ss", f"{s['t']:.3f}", "-i", str(video), "-vf", vf, "-frames:v", "1", "-q:v", "2",
                                 out.name], cwd=d)
        outs.append({"t": s["t"], "file": str(out)})
    return {"thumbs": outs, "message": f"{len(outs)} thumbnail suggestions ready."}
