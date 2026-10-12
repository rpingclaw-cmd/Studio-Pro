"""Background replacement, step 1: find the person in every frame ("matte": white = person, black = background).

Uses Robust Video Matting (RVM, MobileNetV3, ONNX; GPL-3.0, github.com/PeterL1n/RobustVideoMatting), which is
made for video: it remembers earlier frames, so hair and shoulder edges stay steady instead of flickering.
It runs on the preview copy (already converted for normal screens, at most 30 frames a second), in one-minute
chunks so an interrupted run continues where it stopped. The result is studio_work/matte.mp4 (grey picture,
every frame a keyframe so the render can start exactly anywhere). The model (15 MB) is downloaded once.
"""
import hashlib
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import config, ff, hardware, media
from .jobs import Ctx, JobError, file_sig, key_of, mark_done, step_done

MODEL_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
MODEL_SHA256 = "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"
CHUNK_S = 60.0
PREROLL = 12  # frames processed before each chunk so the model has "remembered" the scene
VERSION = 1


def model_path(ctx: Ctx | None = None) -> Path:
    import os
    from . import models
    if os.environ.get("STUDIO_RVM_MODEL") and Path(os.environ["STUDIO_RVM_MODEL"]).is_file():  # (tests)
        return Path(os.environ["STUDIO_RVM_MODEL"])
    f = models.models_dir() / "rvm_mobilenetv3_fp32.onnx"
    if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == MODEL_SHA256:
        return f
    models.download(ctx, MODEL_URL, f, "the person cut-out model (15 MB)", MODEL_SHA256)
    return f


def ratio_for(w: int, h: int) -> float:
    """RVM's own guidance: the model looks at a ~256-pixel version, then refines the edges at full size."""
    return float(min(1.0, max(0.1, 288 / max(w, h))))


class Matter:
    def __init__(self, model: Path, w: int, h: int):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.s = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self.dr = np.array([ratio_for(w, h)], np.float32)
        self.reset()

    def reset(self):
        self.rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        src = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        _fgr, pha, *self.rec = self.s.run(None, {"src": src, "r1i": self.rec[0], "r2i": self.rec[1],
                                                 "r3i": self.rec[2], "r4i": self.rec[3], "downsample_ratio": self.dr})
        return np.clip(pha[0, 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _chunk(ctx: Ctx, m: Matter, proxy: Path, w: int, h: int, fr: Fraction, a: int, b: int, out: Path, done_before: int,
           total: int):
    """Frames a..b (at the preview copy's frame rate) -> out (grey, all keyframes)."""
    pre = min(PREROLL, a)
    t0 = float(Fraction(a - pre) / fr)
    flags = 0x08000000 if config.IS_WIN else 0
    rd = subprocess.Popen([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{t0:.6f}", "-i", str(proxy),
                           "-map", "0:v:0", "-vf", f"scale={w}:{h}", "-fps_mode", "cfr", "-r", str(fr),
                           "-frames:v", str(b - a + pre), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                          stdout=subprocess.PIPE, creationflags=flags)
    tmp = ff.tmp_of(out)
    wr = subprocess.Popen([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                           "-s", f"{w}x{h}", "-r", str(fr), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "14", "-g", "1", "-pix_fmt", "gray", str(tmp)],
                          stdin=subprocess.PIPE, creationflags=flags)
    ctx.job.proc = rd
    m.reset()
    size, i, last = w * h * 3, 0, None
    try:
        while i < b - a + pre:
            buf = rd.stdout.read(size)
            if len(buf) < size:
                break
            alpha = m(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
            if i >= pre:
                wr.stdin.write(alpha.tobytes())
                last = alpha
            i += 1
            if i % 30 == 0:
                ctx.check()
                n = done_before + max(0, i - pre)
                ctx.progress(n / total, f"Finding the person: {int(n / fr // 60)}:{int(n / fr % 60):02d} of "
                                        f"{int(total / fr // 60)}:{int(total / fr % 60):02d}…")
        while last is not None and i - pre < b - a:  # the very end of the video can be a frame short
            wr.stdin.write(last.tobytes())
            i += 1
    finally:
        try:
            wr.stdin.close()
        except OSError:
            pass
        rd.stdout.close()
        rd.wait()
        wr.wait()
    if wr.returncode != 0 or i - pre < 1:
        raise JobError("Could not write the person cut-out. Is the disk full?")
    ff.finish(tmp, out)


def job_matte(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    wd = media.work_dir(str(video))
    proxy = wd / "proxy.mp4"
    if not proxy.exists():
        raise JobError("Run Prepare first (the person is found on the preview copy).")
    info = ff.probe(proxy)
    v = media.stream(info, "video")
    pw, ph = int(v["width"]), int(v["height"])
    if abs(media.rotation(v)) % 180 == 90:
        pw, ph = ph, pw
    s = min(1.0, 960 / max(pw, ph))  # the model works on at most 960 pixels (edges are refined at this size)
    w, h = int(pw * s) // 2 * 2, int(ph * s) // 2 * 2
    fr = Fraction(media.output_fps(v))
    total = int(media.duration(info) * fr)
    out = wd / "matte.mp4"
    key = key_of({"proxy": file_sig(proxy), "w": w, "h": h, "fr": str(fr), "v": VERSION})
    if not step_done(out, key):
        hardware.disk_check(wd, 0.2 + float(total / fr) / 3600 * 1.5, "the person cut-out")
        m = Matter(model_path(ctx), w, h)
        cdir = wd / "matte_parts"
        cdir.mkdir(exist_ok=True)
        step = int(CHUNK_S * fr)
        names = []
        for k, a in enumerate(range(0, total, step)):
            b = min(total, a + step)
            f = cdir / f"m_{k:04d}.mp4"
            names.append(f.name)
            ck = key_of({"base": key, "a": a, "b": b})
            if not step_done(f, ck):
                _chunk(ctx, m, proxy, w, h, fr, a, b, f, a, total)
                mark_done(f, ck)
        lst = cdir / "list.txt"
        lst.write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
        tmp = ff.tmp_of(out)
        ff.run_ff(ctx, ["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", str(tmp.resolve())], cwd=cdir)
        ff.finish(tmp, out)
        mark_done(out, key)
        for f in cdir.glob("m_*"):
            try:
                f.unlink()
            except OSError:
                pass
    cover = _person_share(out)
    return {"matte": str(out), "size": [w, h], "person_pct": cover,
            "message": (f"Person found (about {cover}% of the picture). Choose a background and press “Show a still”."
                        if cover >= 3 else "Hardly any person was found in this video — the background change will "
                                           "not look right. Is the speaker clearly visible?")}


def _person_share(matte: Path) -> float:
    """How much of the picture is the person, on average (a quick sanity check shown to the owner)."""
    r = subprocess.run([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(matte), "-vf", "fps=1/10,scale=64:-2",
                        "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True,
                       creationflags=0x08000000 if config.IS_WIN else 0)
    x = np.frombuffer(r.stdout, np.uint8)
    return round(float(x.mean() / 255 * 100), 1) if len(x) else 0.0


def find(video: str) -> Path | None:
    f = media.work_dir(video) / "matte.mp4"
    return f if f.exists() and Path(str(f) + ".done.json").exists() else None
