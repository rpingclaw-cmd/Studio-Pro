"""Where is the voice? Silero VAD (speech probability every 32 ms) plus a 10 ms loudness map.

Used to put every cut in the quietest spot, to shorten pauses by what you actually hear (not by the gaps
in the transcript), to keep natural breaths, to find "um"s the transcript missed, and (Stage 3) to find
moments where the speaker is silent.
"""
import base64
import json
from pathlib import Path

import numpy as np

from . import audio, ff
from .jobs import Ctx, JobError, key_of, mark_done, read_done, step_done
from .wavio import open_pcm16

MODEL = Path(__file__).parent / "models" / "silero_vad_16k.onnx"
SR, WIN, CTX = 16000, 512, 64  # 32 ms windows, as the model expects


class Silero:
    def __init__(self):
        import onnxruntime
        o = onnxruntime.SessionOptions()
        o.inter_op_num_threads = 1
        o.intra_op_num_threads = 1
        self.s = onnxruntime.InferenceSession(str(MODEL), sess_options=o, providers=["CPUExecutionProvider"])
        self.state = np.zeros((2, 1, 128), np.float32)
        self.ctx = np.zeros((1, CTX), np.float32)
        self.sr = np.array(SR, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self.ctx, chunk[None, :].astype(np.float32)], axis=1)
        out, self.state = self.s.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.ctx = x[:, -CTX:]
        return float(out[0][0])


def speech_probs(path, ctx: Ctx | None = None) -> np.ndarray:
    arr, rate, ch = open_pcm16(path)
    if rate != SR:
        raise JobError("analysis audio must be 16 kHz")
    n = arr.shape[0] // WIN
    m = Silero()
    probs = np.empty(n, np.float32)
    step = 2000
    for i in range(0, n, step):
        if ctx:
            ctx.check()
            ctx.progress(i / max(1, n), "Finding where the voice is (Silero VAD)…")
        j = min(n, i + step)
        block = np.asarray(arr[i * WIN:j * WIN], dtype=np.float32) / 32768.0
        block = block.mean(axis=1) if ch > 1 else block[:, 0]
        for k in range(j - i):
            probs[i + k] = m(block[k * WIN:(k + 1) * WIN])
    return probs


def segments(probs: np.ndarray, on: float = 0.5, off: float = 0.35, min_speech: float = 0.1,
             min_silence: float = 0.1) -> list[list[float]]:
    """Speech stretches in seconds, with hysteresis so a word is not chopped into pieces."""
    hop = WIN / SR
    segs, cur, speaking = [], None, False
    for i, p in enumerate(probs):
        if not speaking and p >= on:
            speaking, cur = True, i
        elif speaking and p < off:
            speaking = False
            segs.append([cur * hop, i * hop])
    if speaking:
        segs.append([cur * hop, len(probs) * hop])
    merged = []
    for s, e in segs:  # close tiny gaps, drop tiny blips
        if merged and s - merged[-1][1] < min_silence:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in merged if e - s >= min_speech]


def envelope_db(path, hop_s: float = 0.01) -> np.ndarray:
    arr, rate, ch = open_pcm16(path)
    hop = int(rate * hop_s)
    n = arr.shape[0] // hop
    out = np.empty(n, np.float32)
    step = 30000
    for i in range(0, n, step):
        j = min(n, i + step)
        x = np.asarray(arr[i * hop:j * hop], dtype=np.float32) / 32768.0
        x = x.mean(axis=1) if ch > 1 else x[:, 0]
        out[i:j] = 10 * np.log10(1e-10 + (x.reshape(j - i, hop) ** 2).mean(axis=1))
    return out


def breaths(env: np.ndarray, speech: list, floor: float) -> list[list[float]]:
    """Short, soft sounds between phrases that are clearly above the room's quiet, but not speech."""
    hop = 0.01
    out = []
    edges = [0.0] + [x for s in speech for x in s] + [len(env) * hop]
    for k in range(0, len(edges) - 1, 2):
        a, b = int(edges[k] / hop), int(edges[k + 1] / hop)
        if b - a < 15:
            continue
        loud = env[a:b] > floor + 10
        i = 0
        while i < len(loud):
            if loud[i]:
                j = i
                while j < len(loud) and loud[j]:
                    j += 1
                d = (j - i) * hop
                if 0.12 <= d <= 0.9:
                    out.append([round((a + i) * hop, 2), round((a + j) * hop, 2)])
                i = j
            else:
                i += 1
    return out


def analyse(ctx: Ctx | None, wav16: Path) -> dict:
    probs = speech_probs(wav16, ctx)
    speech = segments(probs)
    env = envelope_db(wav16)
    live = env[env > -90]
    floor = float(np.percentile(live, 10)) if len(live) else -90.0
    br = breaths(env, speech, floor)
    q = np.clip(np.round(env + 100), 0, 127).astype(np.uint8)  # dB + 100, one byte per 10 ms
    return {"rate": 100, "env_b64": base64.b64encode(q.tobytes()).decode(), "speech": speech, "breaths": br,
            "noise_floor_db": round(floor, 1), "duration": round(len(env) / 100, 2)}


def job_analyse(ctx: Ctx, p: dict) -> dict:
    """Runs on the cleaned sound (so noise doesn't look like voice). Cleans first if needed (cached)."""
    ctx.stage(0, 0.5)
    cl = audio.ensure_clean(ctx, p)
    clean = Path(cl["clean_audio"])
    wd = clean.parent
    out = wd / "analysis.json"
    key = key_of({"clean": read_done(clean).get("key"), "v": 1})
    if not step_done(out, key):
        ctx.stage(0.5, 0.55)
        w16 = wd / "clean_16k.wav"
        tmp = ff.tmp_of(w16)
        ff.run_ff(ctx, ["-i", clean, "-ac", "1", "-ar", SR, "-c:a", "pcm_s16le", tmp], msg="Preparing the analysis…")
        ff.finish(tmp, w16)
        ctx.stage(0.55, 1.0)
        res = analyse(ctx, w16)
        tmp = ff.tmp_of(out)
        tmp.write_text(json.dumps(res), encoding="utf-8")
        ff.finish(tmp, out)
        mark_done(out, key)
    d = json.loads(out.read_text(encoding="utf-8"))
    speech_s = sum(e - s for s, e in d["speech"])
    return {"analysis": str(out), "speech_segments": len(d["speech"]), "breaths": len(d["breaths"]),
            "message": f"Voice found in {len(d['speech'])} stretches ({speech_s / 60:.1f} min of speech), "
                       f"{len(d['breaths'])} breaths."}
