"""Stage 3: face analysis, 16:9 framing that follows the face, punch-ins at cuts, covers, blurred sides,
reels. Needs a real face photo: STUDIO_TEST_FACE=/path/to/face.jpg (skipped otherwise)."""
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from . import media_gen
from .conftest import probe, run

FACE = os.environ.get("STUDIO_TEST_FACE", "/home/user/ext/ocv/samples/data/lena.jpg")
pytestmark = pytest.mark.skipif(not Path(FACE).exists(), reason="needs a face photo (STUDIO_TEST_FACE)")
W, H, DUR = 720, 1280, 40.0


def face_y(t: float) -> float:
    """True face centre (pixels) in the test video: 400 until 18 s, then glides to 700 by 20 s."""
    return 400 + 300 * min(1.0, max(0.0, (t - 18) / 2))


@pytest.fixture(scope="session")
def face_media(tmp_path_factory, eng):
    d = tmp_path_factory.mktemp("face")
    es = media_gen.espeak_talk(DUR + 2)
    speech = es[0] if es else media_gen.speech_like(DUR + 2)
    media_gen.write_wav(d / "voice.wav", speech[: int(DUR * 48000)] * 0.6)
    video = d / "Face talk.mp4"
    y_expr = "400-150+300*min(1\\,max(0\\,(t-18)/2))"  # top of a 300 px face = centre - 150
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x6a7a8a:s={W}x{H}:r=30:d={DUR}",
                    "-loop", "1", "-i", FACE, "-i", d / "voice.wav", "-filter_complex",
                    f"[1:v]scale=300:300[f];[0:v][f]overlay=x=210:y='{y_expr}':shortest=1,format=yuv420p[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-c:a", "aac", "-t", str(DUR), video], check=True)
    run(eng, "prepare", {"video": str(video)})
    return {"video": str(video)}


def frames_at(path, times, w, h):
    out = []
    for t in times:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
        out.append(np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3))
    return out


def detect(img):
    from studio_engine.faces import detector
    h, w = img.shape[:2]
    _, f = detector(w, h).detect(img)
    if f is None or not len(f):
        return None
    f = max(f, key=lambda r: r[2] * r[3])
    return (f[0] + f[2] / 2) / w, (f[1] + f[3] / 2) / h, f[3] / h


def test_faces_found_where_they_are(eng, face_media):
    r = run(eng, "faces", {"video": face_media["video"]})
    assert r["seen_pct"] > 95
    d = json.loads(Path(r["faces"]).read_text())
    errs = [abs((s["face"][1] + s["face"][3] / 2) * H - face_y(s["t"])) / H for s in d["samples"] if s["face"]]
    assert np.percentile(errs, 95) < 0.03, f"face position error {np.percentile(errs, 95):.3f}"


def test_reframe_169_follows_face_with_punch_ins(eng, face_media):
    run(eng, "faces", {"video": face_media["video"]})
    keep = [[0, 9.0], [9.6, 17.0], [17.5, 30.0], [30.4, 39.5]]
    r = run(eng, "render", {"video": face_media["video"], "keep": keep, "sound": {"strength": 0},
                            "settings": {"res": "1080", "layout": "crop169", "punch": 1.18, "enc": "auto"}})
    final = Path(r["outputs"][0])
    v = next(s for s in probe(final)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1920, 1080)
    assert all(l["ok"] for l in r["qc"]["lipsync"] if l["ok"] is not None), r["qc"]["text"]
    # the window never jumps: per-frame moves in every sendcmd file stay under 3% of the picture
    d = Path(face_media["video"]).parent / "studio_work" / "render" / "full"
    for cmd in d.glob("*.cmd"):
        ys = [int(m) for m in re.findall(r"crop@fr y (\d+)", cmd.read_text())]
        assert not ys or np.abs(np.diff(ys)).max() / H < 0.03
    # in the result the face stays well placed (≈40% from the top, centred) and the punch-ins show
    ts = np.arange(0.5, float(r["qc"]["duration_s"]) - 0.5, 1.0)
    found = [detect(f) for f in frames_at(final, ts, 1920, 1080)]
    ok = [f for f in found if f]
    assert len(ok) >= 0.95 * len(found)
    ys = np.array([f[1] for f in ok])
    xs = np.array([f[0] for f in ok])
    assert np.mean((ys > 0.32) & (ys < 0.50)) >= 0.95, ys.round(2).tolist()
    assert np.mean((xs > 1 / 3) & (xs < 2 / 3)) >= 0.95, xs
    starts = np.cumsum([0] + [b - a for a, b in keep])
    sizes = {k: [] for k in range(len(keep))}
    for t, f in zip(ts, found):
        if f:
            k = int(np.searchsorted(starts, t, side="right") - 1)
            if starts[k] + 0.7 < t < starts[k + 1] - 0.7:
                sizes[k].append(f[2])
    normal = np.median(sizes[0] + sizes[2])
    close = np.median(sizes[1] + sizes[3])
    assert 1.1 < close / normal < 1.27, (close, normal)


def test_cover_range_and_title(eng, face_media, tmp_path):
    run(eng, "faces", {"video": face_media["video"]})
    run(eng, "analyse", {"video": face_media["video"], "sound": {"strength": 0}})
    photo = tmp_path / "photo.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=800x600", "-frames:v", "1", photo],
                   check=True)
    covers = [{"start": 0, "end": 6, "mode": "auto"},
              {"start": 14, "end": 18, "mode": "file", "file": str(photo)},
              {"start": 30, "end": 33, "mode": "title", "title": "Divine Light"}]
    r = run(eng, "render", {"video": face_media["video"], "keep": [[0, 40]], "sound": {"strength": 0}, "covers": covers,
                            "settings": {"res": "1080", "layout": "crop169", "punch": 1.0, "enc": "auto"}})
    assert r["qc"]["duration_ok"], r["qc"]["text"]
    assert all(l["ok"] for l in r["qc"]["lipsync"] if l["ok"] is not None), r["qc"]["text"]
    modes = [c["mode"] for c in r["covers"]]
    assert modes[0] in ("auto", "title") and modes[1:] == ["file", "title"], r["covers"]
    final = Path(r["outputs"][0])
    import cv2

    def sharp(im):  # top quarter only (the title text is in the middle)
        return cv2.Laplacian(cv2.cvtColor(im[: im.shape[0] // 4], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    f_photo, f_title, f_after = frames_at(final, [16.0, 31.5, 36.0], 1920, 1080)
    assert detect(f_after) is not None and detect(f_photo) is None  # the photo replaces the speaker
    assert np.abs(f_photo.astype(float) - f_after.astype(float)).mean() > 30  # a different picture
    assert sharp(f_title) < sharp(f_after)  # title card: blurred still behind the text


def test_blurred_sides_and_reel(eng, face_media):
    run(eng, "faces", {"video": face_media["video"]})
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, "
           "Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
           "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: "
           "Words,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00101010,&H78000000,1,0,0,0,100,100,0,0,1,6,2,2,90,150,576,1\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:00.20,0:00:01.00,Words,,0,0,0,,{\\c&H0048AEE0&}Divine{\\c&H00FFFFFF&} light\n")
    r = run(eng, "render", {"video": face_media["video"], "keep": [[0, 20]], "sound": {"strength": 0},
                            "settings": {"res": "1080", "layout": "blur169", "enc": "auto", "reelRes": "1080"},
                            "reels": [{"name": "Reel 01 - Test", "start": 2, "end": 12, "segs": [[2, 6], [6.5, 12]],
                                       "ass": ass}]})
    final, reel = Path(r["outputs"][0]), Path(r["outputs"][-1])
    v = next(s for s in probe(final)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1920, 1080)
    f = detect(frames_at(final, [5.0], 1920, 1080)[0])
    assert f and 0.4 < f[0] < 0.6  # the sharp picture sits in the middle
    rv = next(s for s in probe(reel)["streams"] if s["codec_type"] == "video")
    assert (rv["width"], rv["height"]) == (1080, 1920)
    assert abs(float(probe(reel)["format"]["duration"]) - 9.5) < 0.1

