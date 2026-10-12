"""Background removal / replacement (RVM person cut-out) and the owner's own framing box ("crop the lower body").
Test video: a real person (cut out of a photo once) over a moving, colourful background, swaying a little - so
we know exactly where the person is in every frame. Needs STUDIO_TEST_FACE (a portrait photo, e.g. OpenCV's lena.jpg).
The model downloads once (or set STUDIO_RVM_MODEL=/path/rvm_mobilenetv3_fp32.onnx)."""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from . import media_gen
from .conftest import probe, run

PHOTO = os.environ.get("STUDIO_TEST_FACE", "/home/user/ext/ocv/samples/data/lena.jpg")
pytestmark = pytest.mark.skipif(not Path(PHOTO).exists(), reason="needs a portrait photo (STUDIO_TEST_FACE)")
W, H, DUR, PW = 720, 1280, 30.0, 560   # video size, length, person width
PX, PY = (W - PW) // 2, H - PW          # person's top-left (bottom-centred, like a speaker at a desk)


def sway(t: float) -> int:
    return int(round(10 * np.sin(t * 1.3)))


@pytest.fixture(scope="session")
def person_media(tmp_path_factory, eng):
    import cv2
    from studio_engine import matte
    d = tmp_path_factory.mktemp("person")
    img = cv2.imread(PHOTO)[:, :, ::-1]
    img = cv2.resize(img, (PW, PW))
    m = matte.Matter(matte.model_path(None), PW, PW)
    for _ in range(10):  # the cut-out of the photo, made once: the "true" person shape for this test
        alpha = m(img)
    rgba = np.dstack([img, alpha])
    cv2.imwrite(str(d / "person.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    np.save(d / "alpha.npy", alpha)
    voice = media_gen.speech_like(DUR + 1)
    media_gen.write_wav(d / "voice.wav", voice[: int(DUR * 48000)] * 0.5)
    video = d / "Person talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={DUR}",
                    "-loop", "1", "-i", d / "person.png", "-i", d / "voice.wav", "-filter_complex",
                    f"[1:v]format=rgba[p];[0:v][p]overlay=x='{PX}+10*sin(t*1.3)':y={PY}:shortest=1,format=yuv420p[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
                    "-c:a", "aac", "-t", str(DUR), video], check=True)
    run(eng, "prepare", {"video": str(video)})
    return {"video": str(video), "alpha": alpha}


def known_mask(alpha, t):
    """Where the person is in the test video at time t (full picture size)."""
    m = np.zeros((H, W), np.float32)
    x = PX + sway(t)
    m[PY:PY + PW, x:x + PW] = alpha / 255.0
    return m


def frame(path, t, w, h, fmt="gray"):
    ch = 1 if fmt == "gray" else 3
    seek = ["-ss", f"{t:.3f}"] if t else []  # (no seeking in a single picture)
    raw = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", fmt, "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, ch) if ch > 1 else np.frombuffer(raw, np.uint8).reshape(h, w)


def iou(a, b):
    a, b = a > 0.5, b > 0.5
    return (a & b).sum() / max(1, (a | b).sum())


def test_matte_finds_the_person(eng, person_media):
    import cv2
    r = run(eng, "matte", {"video": person_media["video"]})
    m = Path(r["matte"])
    v = probe(m)["streams"][0]
    mw, mh = int(v["width"]), int(v["height"])
    assert abs(float(probe(m)["format"]["duration"]) - DUR) < 0.2
    for t in (2.0, 11.3, 24.7):  # in step with the picture all the way through
        got = cv2.resize(frame(m, t, mw, mh).astype(np.float32) / 255, (W, H))
        score = iou(got, known_mask(person_media["alpha"], t))
        assert score > 0.85, f"person outline matches only {score:.2f} at {t}s"
    assert 15 < r["person_pct"] < 50, r
    assert run(eng, "matte", {"video": person_media["video"]})["matte"] == r["matte"]  # done once


def test_background_colour_blur_and_picture(eng, person_media, tmp_path):
    run(eng, "matte", {"video": person_media["video"]})
    t = 11.3
    mask = known_mask(person_media["alpha"], t)
    for mode, extra in (("colour", {"colour": "#c81e1e"}), ("blur", {}), ("image", {})):
        bg = {"mode": mode, **extra}
        if mode == "image":
            pic = tmp_path / "bg.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x1e50c8:s=640x360", "-frames:v",
                            "1", pic], check=True)
            bg["file"] = str(pic)
        st = {"res": "orig", "layout": "original", "background": bg}
        out = run(eng, "frame", {"video": person_media["video"], "t": t, "settings": st})["frame"]
        f = frame(out, 0, W, H, "rgb24").astype(float)
        back = f[mask < 0.02].reshape(-1, 3).mean(axis=0)
        src = frame(person_media["video"], t, W, H, "rgb24").astype(float)
        person = np.abs(f - src)[mask > 0.98].mean()
        assert person < 18, f"{mode}: the person changed ({person:.1f})"
        if mode == "colour":
            assert back[0] > 170 and back[1] < 60 and back[2] < 60, back
        if mode == "image":
            assert back[2] > 150 and back[0] < 70, back
        if mode == "blur":  # the busy test pattern behind the person is smoothed
            sharp = lambda x: np.abs(np.diff(x.mean(axis=2), axis=1))[mask[:, 1:] < 0.02].mean()
            assert sharp(f) < sharp(src) * 0.5, (sharp(f), sharp(src))


def test_own_framing_box_crops_the_lower_body(eng, person_media):
    """'Crop the lower part': a box on the top half, same shape as the video -> the top part fills the picture."""
    st = {"res": "orig", "layout": "original", "rect": {"x": 0.25, "y": 0.0, "w": 0.5}}
    out = run(eng, "frame", {"video": person_media["video"], "t": 5, "settings": st})["frame"]
    f = frame(out, 0, W, H, "rgb24").astype(float)
    src = frame(person_media["video"], 5, W, H, "rgb24").astype(float)
    import cv2
    want = cv2.resize(src[0:H // 2, W // 4:W // 4 + W // 2], (W, H), interpolation=cv2.INTER_AREA)
    assert np.abs(f - want).mean() < 12, np.abs(f - want).mean()
    # a different box for one part only (per-clip framing) wins over the whole-video box
    st2 = {**st, "framing_overrides": [{"start": 0, "end": 10, "rect": {"x": 0.0, "y": 0.5, "w": 0.5}}]}
    g = frame(run(eng, "frame", {"video": person_media["video"], "t": 5, "settings": st2})["frame"], 0, W, H, "rgb24").astype(float)
    want2 = cv2.resize(src[H // 2:H, 0:W // 2], (W, H), interpolation=cv2.INTER_AREA)
    assert np.abs(g - want2).mean() < 12
