"""Transcription on this computer (faster-whisper, MIT): every word with its time, no key or upload needed.
The words are given on the video's timeline (seconds; negative = before the camera started, when the
whole recording is kept). Uses the cleaned sound when it exists, else the recorder (lined up), else the
camera sound."""
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from . import config, ff, media, models
from .jobs import Ctx, JobError, file_sig, key_of, mark_done, read_done, step_done

# helps with names and terms of the tradition (only a hint: unknown words are still written as heard)
PROMPT = ("A talk on Pranahuti Yoga and Sahaj Marg: Babuji Maharaj, Lalaji, Sri Ramchandra, Dr K. C. Varadachari, "
          "K. C. Narayana, abhyasi, preceptor, transmission, samskaras, Master, meditation, prayer, cleaning.")


def _source(p: dict, wd) -> tuple[str, float, float, str]:
    """(file, a, b, what): video time = (file time - a) / b. p["source"]: auto | camera."""
    clean = wd / "clean_audio.wav"
    if p.get("source") == "camera":
        cam = wd / "video_16k.wav"
        if not cam.exists():
            raise JobError("Run Prepare first.")
        return str(cam), 0.0, 1.0, "the camera sound"
    if clean.exists() and Path(str(clean) + ".done.json").exists():
        start = float((read_done(clean).get("info") or {}).get("start", 0.0))
        return str(clean), -start, 1.0, "the cleaned sound"
    sync = p.get("sync") or {}
    rec = wd / "audio_16k.wav"
    if p.get("audio") and rec.exists() and sync:
        return str(rec), float(sync.get("a", 0)), float(sync.get("b", 1)), "the recorder"
    cam = wd / "video_16k.wav"
    if not cam.exists():
        raise JobError("Run Prepare first.")
    return str(cam), 0.0, 1.0, "the camera sound"


def read16k(path: str) -> np.ndarray:
    r = subprocess.run([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le",
                        "-"], capture_output=True, creationflags=0x08000000 if config.IS_WIN else 0)
    if r.returncode != 0:
        raise JobError("Could not read the sound for transcription.")
    return np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768.0


_MODELS: dict = {}


def _model(ctx, quality: str):
    """The speech model, loaded once per run of the engine."""
    from faster_whisper import WhisperModel
    if quality not in _MODELS:
        mdir = models.whisper_model(ctx, quality)
        if ctx:
            ctx.progress(0.0, "Loading the speech model…")
        _MODELS.clear()  # keep only one in memory
        _MODELS[quality] = WhisperModel(str(mdir), device="cpu", compute_type="int8",
                                        cpu_threads=max(1, (os.cpu_count() or 2)))
    return _MODELS[quality]


def words_of(ctx: Ctx | None, x: np.ndarray, quality: str, language: str | None, offset_s: float = 0.0,
             progress_total: float | None = None) -> tuple[list[dict], dict]:
    m = _model(ctx, quality)
    segs, info = m.transcribe(x, language=language or None, word_timestamps=True, vad_filter=True,
                              initial_prompt=PROMPT, condition_on_previous_text=True, beam_size=5)
    out = []
    total = progress_total or (len(x) / 16000)
    for s in segs:
        if ctx:
            ctx.check()
            ctx.progress(min(1.0, s.end / max(1.0, total)),
                         f"Transcribing on this computer: {int(s.end // 60)}:{int(s.end % 60):02d} of "
                         f"{int(total // 60)}:{int(total % 60):02d}…")
        for w in s.words or []:
            t = w.word.strip()
            if not t:
                continue
            out.append({"w": t, "s": round(w.start + offset_s, 3), "e": round(w.end + offset_s, 3),
                        "p": round(float(w.probability), 3)})
    return out, {"language": info.language, "language_p": round(float(info.language_probability), 3)}


def job_transcribe(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    wd = media.work_dir(str(video))
    src, a, b, what = _source(p, wd)
    quality = str(p.get("quality") or "accurate")
    lang = (p.get("language") or "").strip() or None
    out = wd / f"transcript_{quality}.json"
    key = key_of({"src": file_sig(src), "a": round(a, 6), "b": round(b, 9), "q": quality, "lang": lang, "v": 1})
    if not step_done(out, key):
        x = read16k(src)
        words, info = words_of(ctx, x, quality, lang)
        for w in words:  # file time -> video time
            w["s"] = round((w["s"] - a) / b, 3)
            w["e"] = round((w["e"] - a) / b, 3)
        tmp = ff.tmp_of(out)
        tmp.write_text(json.dumps({"words": words, "info": info, "source": what}), encoding="utf-8")
        ff.finish(tmp, out)
        mark_done(out, key)
    d = json.loads(out.read_text(encoding="utf-8"))
    n = len(d["words"])
    unsure = sum(1 for w in d["words"] if w.get("p", 1) < 0.5)
    return {"transcript": str(out), "words": d["words"], "info": d["info"],
            "message": f"Transcribed on this computer from {d['source']}: {n:,} words"
                       + (f" ({unsure} to check, underlined)" if unsure else "") + "."}
