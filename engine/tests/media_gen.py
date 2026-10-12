"""Make test media with a KNOWN answer: a camera video (vertical, HEVC 10-bit HLG, variable frame rate,
noisy camera sound) and a separate recorder file that started earlier and whose clock runs slightly fast.

audio_time = A + B * video_time   (A, B are the true values the engine must find)
"""
import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 48000


def speech_like(seconds: float, seed: int = 1) -> np.ndarray:
    """Syllable-like bursts (voiced harmonics + hiss) with word and sentence pauses."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    x = np.zeros(n, np.float32)
    t = 0.3
    while t < seconds - 1:
        for _ in range(rng.integers(4, 14)):  # one sentence
            dur = rng.uniform(0.08, 0.32)
            i0, i1 = int(t * RATE), int(min(seconds, t + dur) * RATE)
            tt = np.arange(i1 - i0) / RATE
            f0 = rng.uniform(95, 190) * (1 + 0.15 * np.sin(2 * np.pi * rng.uniform(1, 4) * tt))
            ph = 2 * np.pi * np.cumsum(f0) / RATE
            phases = rng.uniform(0, 2 * np.pi, 12)  # real voices: harmonics are not all in step
            voiced = sum(np.sin(k * ph + phases[k]) / k ** 1.3 for k in range(1, 12))
            hiss = rng.standard_normal(len(tt)) * (0.3 if rng.random() < 0.3 else 0.05)
            env = np.sin(np.pi * np.arange(len(tt)) / len(tt)) ** 1.5
            x[i0:i1] += (0.25 * voiced + hiss * 0.2) * env * 10 ** rng.uniform(-0.4, 0)  # loud and soft syllables (about 8 dB, like real speech)
            t += dur + rng.uniform(0.03, 0.25)
        t += rng.uniform(0.5, 1.6)
    return x


WORDS = ("the heart is the field of action for the divine light transmission flows from the master into the "
         "abhyasi who meditates with love devotion and surrender every morning and evening constant remembrance "
         "purifies the mind samskaras dissolve slowly real life begins when we turn inward towards the ultimate "
         "reality simplicity is the way and the goal is within").split()


def sentences(n: int, seed: int) -> list[str]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        w = [WORDS[i] for i in rng.integers(0, len(WORDS), rng.integers(5, 14))]
        out.append(" ".join(w).capitalize() + ".")
    return out


def espeak_talk(seconds: float, seed: int = 7) -> tuple[np.ndarray, list[str]] | None:
    """A real-sounding talk (espeak-ng voice) with natural pauses; None if espeak-ng is not installed."""
    import shutil
    import tempfile
    if not shutil.which("espeak-ng"):
        return None
    rng = np.random.default_rng(seed)
    text = sentences(int(seconds / 2.2) + 4, seed)
    ssml = "<speak>" + "".join(f"{t}<break time='{int(rng.uniform(400, 1500))}ms'/>" for t in text) + "</speak>"
    with tempfile.TemporaryDirectory() as d:
        raw, out = Path(d) / "es.wav", Path(d) / "es48.wav"
        subprocess.run(["espeak-ng", "-m", "-v", "en-us", "-s", "150", "-w", raw, ssml], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-ar", str(RATE), "-ac", "1", out], check=True)
        with wave.open(str(out)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
    need = int(seconds * RATE)
    if len(x) < need:
        x = np.concatenate([x, np.zeros(need - len(x), np.float32)])
    x = x[:need]
    return x * (0.9 / (np.abs(x).max() + 1e-9)), text


def pink(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = np.fft.rfft(rng.standard_normal(n))
    f /= np.sqrt(np.arange(1, len(f) + 1))
    y = np.fft.irfft(f, n)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)


def write_wav(path, x: np.ndarray, rate: int = RATE):
    x = np.atleast_2d(x.T).T if x.ndim == 1 else x
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())


def make(folder: Path, video_s: float = 150.0, A: float = 3.217, B: float = 1.00004, hdr: bool = True,
         size: str = "360x640", face: str | None = None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    lead = A / B  # the recorder was running this long before the camera; the talk was already going
    es = espeak_talk(video_s + lead + 10)  # "true" talk; video time t is at index (t + lead)
    speech, script = es if es else (speech_like(video_s + lead + 10, seed=7), None)
    L = int(lead * RATE)
    cam = speech[L: L + int(video_s * RATE)] * 0.5 + pink(int(video_s * RATE), 3) * 0.06
    write_wav(folder / "cam.wav", cam)
    # recorder: started A seconds before the camera, clock B; it runs a bit past the video end
    rec_len = A + B * video_s - 8.0  # ends 8 s early (like the real project: audio shorter than video)
    ta = np.arange(int(rec_len * RATE)) / RATE
    tv = (ta - A) / B
    rec = np.interp(tv + lead, np.arange(len(speech)) / RATE, speech, left=0, right=0).astype(np.float32)
    rec = rec * 0.8 + pink(len(rec), 5) * 0.01
    write_wav(folder / "rec.wav", rec)
    video = folder / "Talk video.mov"
    vf = "setpts='(N+0.35*sin(N/3))/30/TB'"  # uneven frame times = variable frame rate, like a phone
    venc = (["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast", "-x265-params", "log-level=error",
             "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-tag:v", "hvc1"]
            if hdr else ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    # real HDR like an iPhone: the picture is converted to HLG / BT.2020 (not just labelled as HDR)
    to_hlg = (",format=gbrpf32le,zscale=tin=iec61966-2-1:t=linear:npl=100,zscale=p=bt2020:t=arib-std-b67:"
              "m=bt2020nc:r=tv:pin=bt709:min=gbr,format=yuv420p10le") if hdr else ""
    if face:  # a real face (for framing tests) on a plain background
        w, h = (int(x) for x in size.split("x"))
        fs = w * 5 // 12
        graph = (f"color=c=0x6a7a8a:s={size}:r=30:d={video_s}[bg];movie='{face}',loop=-1:1,scale={fs}:{fs}[f];"
                 f"[bg][f]overlay=x='{(w - fs) // 2}+6*sin(t*1.3)':y='{h // 4}+4*sin(t*0.9)':shortest=1,"  # sways a little
                 "noise=alls=5:allf=t,"  # camera-like grain
                 f"{vf.split('=', 1)[0]}={vf.split('=', 1)[1]}{to_hlg}")
        src = ["-f", "lavfi", "-i", graph]
        vfa = []
    else:
        src = ["-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={video_s}"]
        vfa = ["-vf", vf + to_hlg]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-i", folder / "cam.wav", *vfa, "-fps_mode", "passthrough",
                    *venc, "-c:a", "aac", "-b:a", "128k", "-shortest", video], check=True)
    rec_file = folder / "Recorder audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", folder / "rec.wav", "-ar", "44100", "-ac", "2",
                    "-c:a", "aac", "-b:a", "160k", rec_file], check=True)
    return {"video": str(video), "audio": str(rec_file), "A": A, "B": B, "video_s": video_s, "script": script}
