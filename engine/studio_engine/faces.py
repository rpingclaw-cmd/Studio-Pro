"""Look at the picture: where is the face (YuNet), how bright / what colour, how sharp — 5 times a second.

Runs on the small preview copy (fast). Positions are stored as fractions of the picture (0..1), so they
apply to the full-size video too. Used for automatic framing, colour matching and thumbnail suggestions.
"""
import json
import subprocess
from pathlib import Path

import numpy as np

from . import config, ff, media
from .jobs import Ctx, JobError, file_sig, key_of, mark_done, step_done

MODEL = Path(__file__).parent / "models" / "face_detection_yunet.onnx"
FPS = 5


def detector(w: int, h: int):
    import cv2
    d = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.6, 0.3, 50)
    return d


def analyse_video(ctx: Ctx | None, path: Path, fps: float = FPS) -> dict:
    import cv2
    info = ff.probe(path)
    v = media.stream(info, "video")
    if not v:
        raise JobError("No picture in the preview copy — run Prepare again.")
    w, h = int(v["width"]), int(v["height"])
    if abs(media.rotation(v)) % 180 == 90:
        w, h = h, w
    dur = media.duration(info)
    cmd = [ff.tool("ffmpeg"), "-v", "error", "-i", str(path), "-vf", f"fps={fps}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, creationflags=0x08000000 if config.IS_WIN else 0)
    if ctx:
        ctx.job.proc = p
    det = detector(w, h)
    size = w * h * 3
    samples = []
    i = 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            if ctx and i % 25 == 0:
                ctx.check()
                ctx.progress(min(1.0, i / fps / max(1.0, dur)), f"Looking at the picture: {int(i / fps // 60)}:{int(i / fps % 60):02d}…")
            img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            _, faces = det.detect(img)
            face = None
            if faces is not None and len(faces):
                # the speaker: the biggest confident face
                f = max(faces, key=lambda r: r[2] * r[3] * r[14])
                face = [round(float(f[0]) / w, 4), round(float(f[1]) / h, 4), round(float(f[2]) / w, 4),
                        round(float(f[3]) / h, 4), round(float(f[14]), 3)]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            b, g, r = (float(x) for x in img.reshape(-1, 3).mean(axis=0))
            samples.append({"t": round(i / fps, 3), "face": face, "rgb": [round(r, 1), round(g, 1), round(b, 1)],
                            "luma": round(float(gray.mean()), 1), "sharp": round(sharp, 1)})
            i += 1
    finally:
        if p.poll() is None:
            p.terminate()
        p.wait()
    return {"fps": fps, "width": w, "height": h, "duration": dur, "samples": samples}


def job_faces(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    wd = media.work_dir(str(video))
    proxy = wd / "proxy.mp4"
    if not proxy.exists():
        raise JobError("Run Prepare first (it makes the preview copy that is analysed).")
    out = wd / "faces.json"
    key = key_of({"proxy": file_sig(proxy), "v": 1})
    if not step_done(out, key):
        res = analyse_video(ctx, proxy)
        tmp = ff.tmp_of(out)
        tmp.write_text(json.dumps(res), encoding="utf-8")
        ff.finish(tmp, out)
        mark_done(out, key)
    d = json.loads(out.read_text(encoding="utf-8"))
    n = len(d["samples"])
    seen = sum(1 for s in d["samples"] if s["face"])
    return {"faces": str(out), "seen_pct": round(100 * seen / max(1, n), 1),
            "message": f"Picture analysed: a face is visible in {100 * seen / max(1, n):.0f}% of the video."}


def load(video: str) -> dict | None:
    f = media.work_dir(video) / "faces.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
