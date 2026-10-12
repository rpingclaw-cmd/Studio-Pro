"""Stage 3 planning: every output frame is covered exactly once, main picture is continuous, dissolves
keep their progress across cuts, and the framing path is calm."""
from fractions import Fraction

import numpy as np
import pytest

from studio_engine import framing, picture

FR = Fraction(30)


def check(keep, covers_s):
    cov = picture.output_ranges(keep, covers_s, FR)
    pcs = picture.plan(keep, cov, FR)
    total = sum(e - s for s, e in keep)
    assert sum(p.n for p in pcs) == total
    o = 0
    starts = np.cumsum([0] + [e - s for s, e in keep])
    for p in pcs:
        k = int(np.searchsorted(starts, o, side="right") - 1)
        if p.kind in ("main", "in", "out"):
            assert p.src == keep[k][0] + (o - starts[k]), (p, o)
        if p.kind in ("cover", "in", "out"):
            clip = picture.cover_clip_frames(cov, p.cov, FR, total)
            assert 0 <= p.coff and p.coff + p.n <= clip, (p, clip)
        o += p.n
    return cov, pcs


def test_cover_at_start_no_cuts():
    cov, pcs = check([(0, 3000)], [{"start": 0, "end": 60}])
    assert [p.kind for p in pcs] == ["cover", "out", "main"]
    assert pcs[0].n == 1800 - 7 and pcs[1].n == 15


def test_cover_in_middle_across_cuts():
    keep = [(0, 600), (700, 1300), (1400, 2000)]
    cov, pcs = check(keep, [{"start": 15, "end": 40}])
    kinds = [p.kind for p in pcs]
    assert kinds[0] == "main" and "in" in kinds and "out" in kinds and "cover" in kinds
    # the cover's clip offsets run on without gaps
    cv = [p for p in pcs if p.kind in ("in", "cover", "out")]
    for a, b in zip(cv, cv[1:]):
        assert a.coff + a.n == b.coff


def test_dissolve_split_by_cut_keeps_progress():
    keep = [(0, 600), (605, 2000)]  # a cut exactly inside the dissolve window at 20 s
    cov, pcs = check(keep, [{"start": 20.1, "end": 45}])
    ins = [p for p in pcs if p.kind == "in"]
    assert len(ins) == 2 and ins[0].extra["tpos"] == 0 and ins[1].extra["tpos"] == ins[0].n


def test_short_cover_hard_cuts():
    cov, pcs = check([(0, 900)], [{"start": 10, "end": 10.5}])
    assert [p.kind for p in pcs] == ["main", "cover", "main"]


def test_calm_framing_path():
    rng = np.random.default_rng(1)
    t = np.arange(0, 120, 0.2)
    true_y = 0.35 + 0.1 * (t > 60)              # speaker moves once, at 60 s
    noisy = true_y + rng.normal(0, 0.008, len(t))  # detector wobble
    faces = {"fps": 5, "samples": [{"t": float(a), "face": [0.4, float(y) - 0.05, 0.2, 0.1, 0.95]} for a, y in zip(t, noisy)]}
    fr = framing.Framer(faces, 2160, 3840, 16 / 9)
    times = np.arange(0, 120, 1 / 30)
    cw, ch, x, y = fr.at(times)
    assert (cw, ch) == (2160, 1216)
    step = np.abs(np.diff(y)) / 3840
    assert step.max() < 0.015                          # never jumps; glides
    before, after = y[(times > 10) & (times < 55)], y[(times > 70) & (times < 115)]
    assert before.std() < 2 and after.std() < 2       # holds still (no jitter) while the speaker is still
    face_y = (0.35 + 0.1) * 3840
    assert abs((face_y - after.mean()) / ch - framing.LEAD) < 0.01  # face sits 40% down after the move
    # no lag: by one second after the move the frame has arrived
    arrived = y[(times > 61.5) & (times < 62)]
    assert abs((face_y - arrived.mean()) / ch - framing.LEAD) < 0.03
    cw2, ch2, x2, y2 = fr.at(times, zoom=1.18)
    assert cw2 < cw and 0 <= x2.min() and x2.max() + cw2 <= 2160


def test_cover_settings_follow_their_range():
    keep = [(0, 900), (1200, 3000)]          # 30-40 s is cut out
    covers = [{"start": 60, "end": 70, "title": "B"}, {"start": 5, "end": 9, "title": "A"},
              {"start": 31, "end": 35, "title": "gone"}]  # given out of order; the third is inside the cut
    r = picture.output_ranges_idx(keep, covers, FR)
    assert [covers[i]["title"] for _, _, i in r] == ["A", "B"]
