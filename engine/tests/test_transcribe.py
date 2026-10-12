"""Transcription on this computer (faster-whisper): the words are right and land on the video's timeline,
whichever sound they come from (cleaned recorder, mapped through the sync, or the camera's own sound).
The model downloads once from this project's release (or set STUDIO_MODELS_DIR to a folder that has it)."""
import difflib
import re

import pytest

import numpy as np

from .conftest import params, run


def need_voice(media):
    if not media.get("script"):  # synthetic "speech-like" sound has no real words to recognise
        pytest.skip("needs espeak-ng (a speaking voice) to make a test talk with real words")


def toks(ws):
    return [re.sub(r"[^a-z]", "", w["w"].lower()) for w in ws]


def test_transcribe_words_and_timeline(eng, media, synced):
    need_voice(media)
    rec = run(eng, "transcribe", params(media, synced, quality="fast", language="en"), timeout=1800)
    cam = run(eng, "transcribe", {"video": media["video"], "quality": "fast", "language": "en", "source": "camera"},
              timeout=1800)
    assert len(rec["words"]) > 100, rec["message"]
    # the words: compared with the script the synthetic voice read (a robot voice reading random words)
    truth = re.sub(r"[^a-z ]", "", " ".join(media["script"]).lower()).split()
    sm = difflib.SequenceMatcher(None, truth, [t for t in toks(rec["words"]) if t], autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    assert matched / len(toks(rec["words"])) > 0.6, matched
    # the times: the same words heard on the recorder and on the camera are at the same video time
    sm2 = difflib.SequenceMatcher(None, toks(rec["words"]), toks(cam["words"]), autojunk=False)
    diffs = [rec["words"][b.a + k]["s"] - cam["words"][b.b + k]["s"] for b in sm2.get_matching_blocks() for k in range(b.size)]
    assert len(diffs) > 50
    # (speech-recogniser word times are good to about +-0.1 s; the exact sync is checked to the millisecond elsewhere)
    assert abs(float(np.median(diffs))) < 0.15, np.median(diffs)
    # all inside the video (the recorder started earlier, but those words are before 0 only if the whole recording is kept)
    assert min(w["s"] for w in rec["words"]) > -media["A"] / media["B"] - 0.5  # (before 0: recorder ran before the camera)
    again = run(eng, "transcribe", params(media, synced, quality="fast", language="en"))
    assert again["words"] == rec["words"]  # done once, then re-used


def test_sync_words_agree(media, synced):
    """The normal sync also checks the words at 3 points (same sentences heard at the same moment)."""
    need_voice(media)
    w = synced.get("words") or {}
    assert w.get("checked") == 3 and w.get("agree") == 3, w
    assert "Words agree at 3 of 3 points" in synced["message"]
    for p in w["points"]:
        assert abs(p["err_ms"]) < 80, w


def test_sync_from_words_when_sound_pattern_fails(eng, media, prepared, monkeypatch):
    """Backup: if the loudness patterns can't be matched, the words find the sync, then the exact sound
    comparison fine-tunes it (< 5 ms at start, middle and end)."""
    need_voice(media)
    from studio_engine import syncer
    from studio_engine.jobs import JobError

    def fail(*a, **k):
        raise JobError("no match")
    monkeypatch.setattr(syncer, "compute", fail)
    r = run(eng, "sync", {"video_wav": prepared["video_wav"], "audio_wav": prepared["audio_wav"]}, timeout=1800)
    assert r.get("by_words") and r["confident"], r
    A, B = media["A"], media["B"]
    for tv in (5.0, media["video_s"] / 2, media["video_s"] - 8):
        err = abs((r["a"] + r["b"] * tv) - (A + B * tv)) * 1000
        assert err < 5, f"word-based sync off by {err:.1f} ms at {tv}s: {r}"
