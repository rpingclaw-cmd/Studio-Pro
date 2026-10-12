"""Running ffmpeg / ffprobe / deep-filter with live progress, cancel and readable errors."""
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from . import config
from .jobs import Cancelled, Ctx, JobError


def tool(name: str) -> str:
    p = config.find_tool(name)
    if not p:
        if name in ("ffmpeg", "ffprobe"):
            raise JobError("ffmpeg is not installed. Run the Studio Engine installer again (it installs ffmpeg).")
        raise JobError(f"{name} is missing. Run the Studio Engine installer again.")
    return p


def tmp_of(out: Path) -> Path:
    """Write to 'name.part.ext' first, so a half-written file is never mistaken for a finished one."""
    return out.with_name(out.stem + ".part" + out.suffix)


def finish(tmp: Path, out: Path) -> None:
    os.replace(tmp, out)


def probe(path) -> dict:
    cmd = [tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=0x08000000 if config.IS_WIN else 0)
    if r.returncode != 0:
        raise JobError(f"Could not read {Path(path).name}: {r.stderr.strip()[-300:] or 'unknown format'}")
    return json.loads(r.stdout)


def _hms(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def run_ff(ctx: Ctx, args: list[str], duration: float | None = None, msg: str = "", loglevel: str = "error",
           want_stderr: bool = False, cwd=None) -> str:
    """Run ffmpeg. Progress (0..1 of `duration` seconds of output) goes to ctx. Returns stderr if asked."""
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel, "-progress", "pipe:1",
           "-nostats"] + [str(a) for a in args]
    p = ctx.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                  errors="replace", cwd=cwd)
    err: list[str] = []

    def read_err():
        for line in p.stderr:
            err.append(line)
            if len(err) > 4000 and not want_stderr:
                del err[:2000]

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    import time
    t0 = time.time()
    for line in p.stdout:
        if ctx.job.cancel_flag.is_set():
            p.terminate()
            break
        if duration and line.startswith("out_time_us="):
            try:
                sec = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            spent = time.time() - t0
            speed = f" · {sec / spent:.1f}× real time" if spent > 20 and sec > 0 else ""
            ctx.progress(sec / duration, f"{msg} {_hms(sec)} of {_hms(duration)}{speed}" if msg else None)
    p.wait()
    t.join(timeout=5)
    if ctx.job.cancel_flag.is_set():
        raise Cancelled()
    text = "".join(err)
    if p.returncode != 0:
        ctx.log(text[-4000:])
        raise JobError(friendly_error(text) or f"ffmpeg stopped with an error (code {p.returncode}).")
    return text


def friendly_error(text: str) -> str:
    t = text.lower()
    if "no space left" in t:
        return "The disk is full. Free some space (or choose a folder on a bigger drive) and press Resume."
    if "permission denied" in t:
        return "Windows refused to write a file (permission denied). Close any program that has the file open and press Resume."
    if "no such file" in t:
        return "A file could not be found. Was it moved or renamed? Choose it again and retry."
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return ("ffmpeg error: " + lines[-1][:300]) if lines else ""


def run_tool(ctx: Ctx, cmd: list[str], msg: str = "", expect_seconds: float | None = None) -> str:
    """Run another program (deep-filter). Progress is estimated from expected run time."""
    import time
    p = ctx.popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                  encoding="utf-8", errors="replace")
    out: list[str] = []
    t = threading.Thread(target=lambda: out.extend(p.stdout), daemon=True)
    t.start()
    t0 = time.time()
    while p.poll() is None:
        if ctx.job.cancel_flag.is_set():
            p.terminate()
            p.wait()
            raise Cancelled()
        if expect_seconds:
            ctx.progress(min(0.97, (time.time() - t0) / expect_seconds), msg or None)
        time.sleep(0.25)
    t.join(timeout=5)
    text = "".join(out)
    if p.returncode != 0:
        ctx.log(text[-4000:])
        raise JobError(f"{Path(cmd[0]).name} stopped with an error: {text.strip().splitlines()[-1][:300] if text.strip() else p.returncode}")
    return text


def parse_json_block(text: str, must_have: str) -> dict:
    """loudnorm prints a JSON object into the log; pull it out."""
    for m in re.finditer(r"\{[^{}]*\}", text, re.S):
        if must_have in m.group(0):
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise JobError("Could not read the loudness measurement from ffmpeg.")
