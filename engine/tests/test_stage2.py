"""Stage 2: voice map (Silero VAD + loudness map) and smooth, exact cuts."""
import base64
import json
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from .conftest import params, run


def test_analyse_finds_the_voice(eng, media, synced):
    if not media.get("script"):
        pytest.skip("needs espeak-ng for a realistic voice")
    r = run(eng, "analyse", params(media, synced))
    d = json.loads(Path(r["analysis"]).read_text())
    sp = d["speech"]
    # the espeak talk has one stretch per sentence (pauses 0.4-1.5 s); allow some merging/splitting
    n_sent = sum(1 for _ in media["script"])
    assert len(sp) >= 0.5 * (media["video_s"] / 3.4), len(sp)
    env = np.frombuffer(base64.b64decode(d["env_b64"]), np.uint8).astype(float) - 100
    loud = env > env.max() - 30
    covered = np.zeros_like(loud)
    for s, e in sp:
        covered[int(s * 100):int(e * 100) + 1] = True
    cov = (loud & covered).sum() / max(1, loud.sum())
    assert cov > 0.95, f"only {cov:.0%} of the voice is inside detected speech"
    silent = (~covered).sum() / len(covered)
    assert 0.1 < silent < 0.6, f"silence share {silent:.0%}"
    assert abs(d["duration"] - media["video_s"]) < 0.5


def _tone_wav(path, seconds=10.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    x = 0.5 * np.sin(2 * np.pi * 220 * t + 3 * np.sin(2 * np.pi * 0.7 * t))  # changes all the time
    st = np.stack([x, x], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes((st * 32767).astype("<i2").tobytes())


@pytest.mark.parametrize("xfade", [0, 40, 120])
def test_cuts_exact_length_and_smooth(tmp_path, xfade):
    from studio_engine.render import assemble_audio
    src = tmp_path / "src.wav"
    _tone_wav(src)
    fr = Fraction(30)
    segs = [(0, 31), (45, 61), (90, 92), (120, 270)]  # frames; includes a very short part
    out = tmp_path / "out.wav"
    assemble_audio(src, segs, fr, out, xfade)
    with wave.open(str(out)) as w:
        n = w.getnframes()
        y = np.frombuffer(w.readframes(n), "<i2").reshape(-1, 2)[:, 0].astype(float) / 32768
    frames = sum(b - a for a, b in segs)
    assert n == int(round(Fraction(frames) / fr * 48000)), "length must match the picture exactly"
    # no clicks: the biggest step between samples stays near what the tone itself has
    t = np.arange(48000 * 10) / 48000
    ref = 0.5 * np.sin(2 * np.pi * 220 * t + 3 * np.sin(2 * np.pi * 0.7 * t))
    assert np.abs(np.diff(y)).max() < 2.5 * np.abs(np.diff(ref)).max()
    # inside each part (away from the joins) the sound is exactly the source: lip sync is kept
    cum = 0
    for a, b in segs:
        o = int(round(Fraction(cum) / fr * 48000))
        mid_o = o + int(round(Fraction(b - a) / fr * 48000)) // 2
        mid_s = int(round(Fraction(a) / fr * 48000)) + (mid_o - o)
        assert abs(y[mid_o] - ref[mid_s]) < 1e-3
        cum += b - a
