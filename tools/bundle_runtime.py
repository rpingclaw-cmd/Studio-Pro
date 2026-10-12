#!/usr/bin/env python3
"""Gather what the Studio Pro installer carries, into desktop/build/:
  python/  a private Python 3.13 (python-build-standalone, via uv) with the engine's libraries
  bin/     ffmpeg + ffprobe (BtbN GPL builds, FFmpeg 8.1) and deep-filter 0.5.6 (DeepFilterNet)
Run on the same kind of computer as the installer being built (GitHub Actions does this for each one):
  python3 tools/bundle_runtime.py            (needs uv)
"""
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop" / "build"
PY_VERSION = "3.13"
FFMPEG = {
    "win": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
    "linux": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz",
}
DEEP_FILTER = {
    "win": "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-pc-windows-msvc.exe",
    "linux": "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl",
    "mac-arm64": "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-aarch64-apple-darwin",
    "mac-x64": "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-apple-darwin",
}
OS = "win" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "linux"
EXE = ".exe" if OS == "win" else ""


def get(url: str) -> bytes:
    print("download", url, flush=True)
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def run(*cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def python_runtime():
    dst = OUT / "python"
    shutil.rmtree(dst, ignore_errors=True)
    with tempfile.TemporaryDirectory() as td:
        run("uv", "python", "install", PY_VERSION, "--install-dir", td, "--no-bin")
        found = [p for p in Path(td).iterdir() if p.is_dir() and p.name.startswith("cpython-")]
        if not found:
            sys.exit("uv did not install Python")
        shutil.copytree(found[0], dst, symlinks=True)
    py = dst / ("python.exe" if OS == "win" else "bin/python3")
    for marker in dst.rglob("EXTERNALLY-MANAGED"):  # this Python belongs to the app: allow installing into it
        marker.unlink()
    req = OUT / "requirements.txt"
    run("uv", "export", "--project", ROOT / "engine", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
        "-o", req)
    run("uv", "pip", "install", "--python", py, "--no-cache", "-r", req)
    req.unlink()
    # slim it down: tests, caches and the headers/static libs only needed to build things
    for pat in ("**/__pycache__", "**/tests", "**/test", "include", "lib/pkgconfig", "**/idlelib", "**/turtledemo",
                "**/ensurepip"):
        for p in dst.glob(pat):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    run(py, "-c", "import numpy, onnxruntime, cv2, fastapi, uvicorn; print('python ok', numpy.__version__)")


def tools():
    b = OUT / "bin"
    shutil.rmtree(b, ignore_errors=True)
    b.mkdir(parents=True)
    if OS in FFMPEG:
        data = get(FFMPEG[OS])
        names = {f"ffmpeg{EXE}", f"ffprobe{EXE}"}
        if FFMPEG[OS].endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.namelist():
                    if Path(m).name in names and "/bin/" in m:
                        (b / Path(m).name).write_bytes(z.read(m))
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
                for m in t.getmembers():
                    if Path(m.name).name in names and "/bin/" in m.name:
                        (b / Path(m.name).name).write_bytes(t.extractfile(m).read())
        # FFmpeg's licence travels with it
        (b / "FFMPEG-LICENSE.txt").write_text("FFmpeg (https://ffmpeg.org), GPL build by BtbN "
                                              "(https://github.com/BtbN/FFmpeg-Builds). Licence: GPL-3.0.\n")
    else:  # Mac: no BtbN build — use the evermeet / Homebrew ffmpeg found on the build computer
        for n in ("ffmpeg", "ffprobe"):
            src = shutil.which(n)
            if src:
                shutil.copy2(src, b / n)
    key = OS if OS != "mac" else ("mac-arm64" if platform.machine() == "arm64" else "mac-x64")
    (b / f"deep-filter{EXE}").write_bytes(get(DEEP_FILTER[key]))
    for f in b.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    run(b / f"ffmpeg{EXE}", "-hide_banner", "-version")
    missing = [n for n in (f"ffmpeg{EXE}", f"ffprobe{EXE}", f"deep-filter{EXE}") if not (b / n).exists()]
    if missing:
        sys.exit("missing tools: " + ", ".join(missing))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("UV_PYTHON_DOWNLOADS", "automatic")
    which = sys.argv[1:] or ["python", "tools"]
    if "python" in which:
        python_runtime()
    if "tools" in which:
        tools()
    print("bundle ready in", OUT)
