"""AI model files the engine downloads once (kept in the app's folder under models/).
Whisper models come from this project's own GitHub release (copies of the MIT-licensed faster-whisper
conversions), so they download from GitHub like the app itself."""
import hashlib
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

from . import config
from .jobs import Ctx, JobError

RELEASE = "https://github.com/rpingclaw-cmd/Studio-Pro/releases/download/models-v1"
WHISPER = {  # quality setting -> (model folder, approximate download size)
    "fast": ("whisper-base", "140 MB"),
    "accurate": ("whisper-small", "460 MB"),
    "best": ("whisper-large-v3-turbo", "1.5 GB"),
}


def models_dir() -> Path:
    d = Path(os.environ.get("STUDIO_MODELS_DIR") or (config.app_dir() / "models"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(ctx: Ctx | None, url: str, dst: Path, label: str, sha256: str | None = None):
    tmp = dst.with_name(dst.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 20):
                out.write(chunk)
                got += len(chunk)
                if ctx:
                    ctx.check()
                    if total:
                        ctx.progress(got / total, f"Downloading {label} (once): {got / 2**20:.0f} of {total / 2**20:.0f} MB…")
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise JobError(f"Could not download {label} ({e}). Check the internet connection and press Resume.")
    if sha256 and hashlib.sha256(tmp.read_bytes()).hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise JobError(f"The downloaded {label} was damaged. Please press Resume to try again.")
    tmp.replace(dst)


def whisper_model(ctx: Ctx | None, quality: str) -> Path:
    name, size = WHISPER.get(quality, WHISPER["accurate"])
    d = models_dir() / name
    if (d / "model.bin").exists() and (d / "config.json").exists():
        return d
    z = models_dir() / f"{name}.zip"
    download(ctx, f"{RELEASE}/{name}.zip", z, f"the speech model “{quality}” ({size})")
    tmp = d.with_name(d.name + ".unpack")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(tmp)
    shutil.rmtree(d, ignore_errors=True)
    tmp.replace(d)
    z.unlink(missing_ok=True)
    if not (d / "model.bin").exists():
        raise JobError("The speech model download was incomplete. Please press Resume.")
    return d
