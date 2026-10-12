"""Pro tools: titles / lower thirds, background music that dips under the voice, smooth dissolves at cuts, a slow
zoom on one clip, a quieter voice on another, extra copies (WhatsApp, square), colour wheels and a LUT."""
import subprocess
from pathlib import Path

import numpy as np

from .conftest import params, probe, run

TITLE_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lower,DejaVu Sans,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,3,40,0,1,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.00,0:00:06.00,Lower,,0,0,0,,Divine Light Meditation
"""


def frame(path, t, fmt="rgb24"):
    v = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    seek = ["-ss", f"{t:.3f}"] if t else []
    raw = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", fmt,
                          "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(float)


def pcm(path, rate=16000):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768


def band_db(x, rate, lo, hi, win=0.5):
    n = int(rate * win)
    out = []
    for i in range(0, len(x) - n, n):
        f = np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))
        fq = np.fft.rfftfreq(n, 1 / rate)
        out.append(10 * np.log10(1e-12 + (f[(fq >= lo) & (fq < hi)] ** 2).sum()))
    return np.array(out)


def test_pro_render(eng, media, synced, tmp_path):
    music = tmp_path / "music.wav"
    # (music below the voice's range, so it can be measured separately from the voice)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=55:d=20", "-f", "lavfi", "-i",
                    "sine=f=65:d=20", "-filter_complex", "amix=inputs=2,volume=0.5", "-ar", "48000", music], check=True)
    keep = [[0.0, 30.0], [40.0, 70.0], [80.0, 110.0]]
    st = {"res": "orig", "layout": "original", "enc": "auto", "cut_style": "dissolve", "punch": 1,
          "framing_overrides": [{"start": 40, "end": 70, "kb": 0.2}], "extras": ["whatsapp", "square"]}
    p = params(media, synced, keep=keep, settings=st, titles_ass=TITLE_ASS,
               music={"file": str(music), "db": -14, "duck": True, "duck_db": 14, "fade_in": 0.5, "fade_out": 1},
               voice_gains=[{"start": 80, "end": 110, "db": -12}])
    r = run(eng, "render", p, timeout=1800)
    qc = r["qc"]
    assert qc["duration_ok"] and qc["loudness_ok"], qc["text"]
    measured = [l for l in qc["lipsync"] if l.get("ok") is not None]
    assert measured and all(abs(l["offset_ms"]) < 5 for l in measured), qc["text"]
    final = Path(r["outputs"][0])
    W, H = (lambda v: (int(v["width"]), int(v["height"])))(next(s for s in probe(final)["streams"] if s["codec_type"] == "video"))

    # titles: a black box with white text in the lower part from 2 s to 6 s, not afterwards
    def box(t):
        f = frame(final, t)[int(H * 0.80):int(H * 0.93), : W // 2]
        lum = f.mean(axis=2)
        return (lum < 20).mean(), (lum > 230).mean()
    dark, light = box(3.5)
    assert dark > 0.25 and light > 0.01, (dark, light)
    assert box(9.0)[0] < 0.1

    # music: there, and ducked while the voice speaks (first part, normal voice level)
    x = pcm(final)[: 16000 * 30]
    mus, voice = band_db(x, 16000, 45, 75), band_db(x, 16000, 600, 3000)
    assert np.median(mus) > np.median(band_db(pcm(media["audio"])[: 16000 * 30], 16000, 45, 75)) + 6  # music added
    loud, quiet = voice > np.percentile(voice, 70), voice < np.percentile(voice, 30)
    assert mus[quiet].mean() - mus[loud].mean() > 3, (mus[quiet].mean(), mus[loud].mean())

    # dissolve at the cut (output 30 s): halfway through, the picture is a mix of both sides
    # (reference pictures: the same moments made as stills - same HDR conversion and colour, no dissolve or zoom)
    ref = lambda t: frame(run(eng, "frame", {"video": media["video"], "t": t, "settings": {"res": "orig", "layout": "original"}})["frame"], 0)
    out = frame(final, 30.0)
    a, b = ref(30.0), ref(40.0)
    def mse(u, v):
        return float(((u - v) ** 2).mean())
    # (stills and video are compressed differently, so nothing matches exactly; the 50/50 mix must match clearly best)
    assert mse(out, (a + b) / 2) < min(mse(out, a), mse(out, b)) * 0.75, (mse(out, (a + b) / 2), mse(out, a), mse(out, b))

    # slow zoom on the second clip: at its end the picture is zoomed in (no longer equals the plain frame)
    start2 = frame(final, 31.0)
    end2 = frame(final, 59.5)
    r41, r69 = ref(41.0), ref(69.5)
    # (start of the clip: hardly zoomed - only compression differences; end: 20 % zoomed - clearly different)
    assert mse(end2, r69) > 2.5 * mse(start2, r41), (mse(end2, r69), mse(start2, r41))

    # quieter voice on the third clip: about 12 dB down
    y = pcm(final)
    rms = lambda s, e: 20 * np.log10(np.sqrt((y[int(s * 16000):int(e * 16000)] ** 2).mean()) + 1e-9)
    x0 = pcm(media["video"])
    rms0 = lambda s, e: 20 * np.log10(np.sqrt((x0[int(s * 16000):int(e * 16000)] ** 2).mean()) + 1e-9)
    drop = (rms(5, 25) - rms(65, 85)) - (rms0(5, 25) - rms0(85, 105))
    if media.get("script"):  # (real-sounding speech: synthetic noise is changed too much by the voice cleaning)
        assert 8 < drop < 16, drop

    # extra copies
    names = [Path(o).name for o in r["outputs"]]
    assert "Edited - full video (WhatsApp).mp4" in names and "Edited - full video (square).mp4" in names
    sq = next(s for s in probe(final.with_name("Edited - full video (square).mp4"))["streams"] if s["codec_type"] == "video")
    assert (sq["width"], sq["height"]) == (1080, 1080)
    wa = next(s for s in probe(final.with_name("Edited - full video (WhatsApp).mp4"))["streams"] if s["codec_type"] == "video")
    assert int(wa["height"]) <= 720


def test_colour_wheels_and_lut(eng, media, tmp_path):
    base = {"res": "orig", "layout": "original"}
    still = lambda st: frame(run(eng, "frame", {"video": media["video"], "t": 20, "settings": st})["frame"], 0)
    plain = still(base)
    warm = still({**base, "colour": {"auto": False, "gain": {"r": 0.6, "b": -0.6}, "lift": {"r": 0.2}}})
    rb = lambda f: f[..., 0].mean() / max(1.0, f[..., 2].mean())
    assert rb(warm) > rb(plain) * 1.2, (rb(warm), rb(plain))
    assert warm[..., 2].mean() < plain[..., 2].mean() - 10
    # a LUT that swaps red and blue
    n = 2
    lines = ["LUT_3D_SIZE 2"]
    for bb in range(n):
        for gg in range(n):
            for rr in range(n):
                lines.append(f"{bb:.1f} {gg:.1f} {rr:.1f}")
    lut = tmp_path / "swap.cube"
    lut.write_text("\n".join(lines) + "\n")
    swapped = still({**base, "colour": {"auto": False, "lut": str(lut)}})
    assert abs(swapped[..., 0].mean() - plain[..., 2].mean()) < 6 and abs(swapped[..., 2].mean() - plain[..., 0].mean()) < 6
