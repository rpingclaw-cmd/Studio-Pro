"""Shared test fixtures: synthetic media with a known answer, an in-process engine, helpers."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from . import media_gen

HAVE_DF = bool(os.environ.get("STUDIO_DEEP_FILTER"))
STRENGTH = 70 if HAVE_DF else 0


@pytest.fixture(scope="session")
def eng(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    os.environ["STUDIO_ENGINE_HOME"] = str(home)
    os.environ["STUDIO_DFN_CHUNK_S"] = "45"  # several parallel parts even in a short test
    from studio_engine import server
    server.TOKEN = "test-token"
    return server


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    return media_gen.make(tmp_path_factory.mktemp("media"), video_s=150.0)


def run(eng, type_, params, timeout=900):
    m = eng.manager()
    job = m.submit(type_, params)
    t0 = time.time()
    while job.state in ("queued", "running"):
        assert time.time() - t0 < timeout, "job took too long"
        time.sleep(0.1)
    assert job.state == "done", f"{type_} {job.state}: {job.error}\n" + "\n".join(job.log_lines[-30:])
    return job.result


def probe(path):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(path)], capture_output=True, text=True).stdout)


@pytest.fixture(scope="session")
def prepared(eng, media):
    return run(eng, "prepare", {"video": media["video"], "audio": media["audio"]})


@pytest.fixture(scope="session")
def synced(eng, media, prepared):
    return run(eng, "sync", {"video_wav": prepared["video_wav"], "audio_wav": prepared["audio_wav"]})


def params(media, synced, **kw):
    return {"video": media["video"], "audio": media["audio"], "sync": {"a": synced["a"], "b": synced["b"]},
            "sound": {"strength": STRENGTH, "lufs": -16}, **kw}


