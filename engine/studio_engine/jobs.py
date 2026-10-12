"""Job queue: one heavy job at a time (this is a laptop), live progress, cancel, and resume.

Every job is made of steps that write their result files only when finished (temp name, then rename),
next to a small ".done.json" that records the settings used. Running the same job again skips every
step that is already done with the same settings, so an interrupted job continues where it stopped.
"""
import hashlib
import json
import math
import os
import queue
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config


class Cancelled(Exception):
    pass


class JobError(Exception):
    """An error with a message meant for the owner (plain words)."""


def _plain(o):
    """numpy numbers etc. -> normal JSON values."""
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _finite(o):
    """JSON cannot carry inf / nan (e.g. the noise floor of digital silence is -inf dB): make them null."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_finite(v) for v in o]
    return o


def key_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_sig(p) -> dict:
    st = Path(p).stat()
    return {"path": str(Path(p).resolve()), "size": st.st_size, "mtime": int(st.st_mtime)}


def step_done(out: Path, key: str) -> bool:
    meta = Path(str(out) + ".done.json")
    if not (out.exists() and meta.exists()):
        return False
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("key") == key
    except (OSError, ValueError):
        return False


def mark_done(out: Path, key: str, extra: dict | None = None) -> None:
    Path(str(out) + ".done.json").write_text(json.dumps({"key": key, "at": time.time(), **(extra or {})}),
                                             encoding="utf-8")


def read_done(out: Path) -> dict:
    try:
        return json.loads(Path(str(out) + ".done.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class _KeepAwake:
    """Stop the computer going to sleep while a job runs (Windows / Mac)."""

    def __init__(self):
        self._proc = None

    def __enter__(self):
        try:
            if config.IS_WIN:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS|SYSTEM_REQUIRED
            elif config.IS_MAC:
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except Exception:
            pass
        return self

    def __exit__(self, *a):
        try:
            if config.IS_WIN:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self._proc:
                self._proc.terminate()
        except Exception:
            pass


class Job:
    def __init__(self, type_: str, params: dict, id_: str | None = None):
        self.id = id_ or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.type = type_
        self.params = params
        self.state = "queued"  # queued | running | done | failed | cancelled | interrupted
        self.progress = 0.0
        self.message = "Waiting…"
        self.result = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancel_flag = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.log_lines: list[str] = []

    def public(self) -> dict:
        return {"id": self.id, "type": self.type, "state": self.state, "progress": round(self.progress, 4),
                "message": self.message, "result": self.result, "error": self.error, "created": self.created,
                "started": self.started, "finished": self.finished, "params": self.params,
                "title": self.params.get("title") or self.type}


class Ctx:
    """Handed to each job function: report progress, run tools, check for cancel."""

    def __init__(self, mgr: "JobManager", job: Job):
        self.mgr, self.job = mgr, job
        self._lo, self._hi = 0.0, 1.0

    def stage(self, lo: float, hi: float):
        """Map the next progress reports (0..1) into the range lo..hi of the whole job."""
        self._lo, self._hi = lo, hi

    def progress(self, frac: float | None = None, msg: str | None = None):
        if frac is not None:
            self.job.progress = max(self.job.progress, min(1.0, self._lo + (self._hi - self._lo) * max(0.0, min(1.0, frac))))
        if msg:
            self.job.message = msg
        self.mgr.changed(self.job)

    def log(self, text: str):
        line = time.strftime("%H:%M:%S ") + text
        self.job.log_lines.append(line)
        self.job.log_lines = self.job.log_lines[-400:]
        try:
            with open(self.mgr.log_path(self.job), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def check(self):
        if self.job.cancel_flag.is_set():
            raise Cancelled()

    def popen(self, cmd: list[str], **kw) -> subprocess.Popen:
        self.check()
        self.log("RUN " + " ".join(cmd))
        flags = 0
        if config.IS_WIN:
            # BELOW_NORMAL priority keeps the laptop usable. The tools stay attached to the engine's window,
            # so closing that window stops them too.
            flags = 0x00004000
        p = subprocess.Popen(cmd, creationflags=flags, **kw)
        if not config.IS_WIN:
            try:
                os.setpriority(os.PRIO_PROCESS, p.pid, 5)
            except (OSError, AttributeError):
                pass
        self.job.proc = p
        return p


class JobManager:
    def __init__(self, handlers: dict):
        self.handlers = handlers
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()
        self.version = 0
        self.cond = threading.Condition()
        self.dir = config.app_dir() / "jobs"
        self.dir.mkdir(exist_ok=True)
        self._load_old()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------- persistence ----------
    def log_path(self, job: Job) -> Path:
        return self.dir / f"{job.id}.log"

    def _save(self, job: Job):
        try:
            tmp = self.dir / f"{job.id}.json.tmp"
            tmp.write_text(json.dumps(job.public(), default=_plain), encoding="utf-8")
            os.replace(tmp, self.dir / f"{job.id}.json")
        except (OSError, TypeError, ValueError) as e:
            job.log_lines.append(f"could not save job state: {e}")

    def _load_old(self):
        files = sorted(self.dir.glob("*.json"))
        for f in files[:-60]:  # keep the job list short
            for p in (f, f.with_suffix(".log")):
                try:
                    p.unlink()
                except OSError:
                    pass
        for f in files[-60:]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            j = Job(d["type"], d.get("params") or {}, d["id"])
            j.state, j.progress, j.message = d["state"], d.get("progress", 0), d.get("message", "")
            j.result, j.error, j.created = _finite(d.get("result")), d.get("error"), d.get("created", 0)
            j.started, j.finished = d.get("started"), d.get("finished")
            if j.state in ("queued", "running"):
                j.state = "interrupted"
                j.message = "Stopped when the engine was closed. Press Resume to continue where it stopped."
            self.jobs[j.id] = j

    # ---------- public ----------
    def changed(self, job: Job):
        with self.cond:
            self.version += 1
            self.cond.notify_all()
        self._save(job)

    def submit(self, type_: str, params: dict) -> Job:
        if type_ not in self.handlers:
            raise JobError(f"Unknown job type: {type_}")
        job = Job(type_, params)
        with self.lock:
            self.jobs[job.id] = job
        self.changed(job)
        self.q.put(job.id)
        return job

    def resume(self, job_id: str) -> Job:
        old = self.jobs[job_id]
        return self.submit(old.type, old.params)

    def cancel(self, job_id: str):
        job = self.jobs[job_id]
        job.cancel_flag.set()
        if job.state == "queued":
            job.state, job.message = "cancelled", "Cancelled."
            self.changed(job)
        p = job.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def list(self) -> list[dict]:
        return [_finite(j.public()) for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]

    def busy(self) -> bool:
        return any(j.state == "running" for j in self.jobs.values())

    # ---------- worker ----------
    def _worker(self):
        while True:
            job = self.jobs.get(self.q.get())
            if not job or job.state != "queued":
                continue
            try:
                self._run(job)
            except Exception:  # never let the worker die: the next jobs must still run
                traceback.print_exc()

    def _run(self, job: Job):
        job.state, job.started, job.message = "running", time.time(), "Starting…"
        self.changed(job)
        ctx = Ctx(self, job)
        try:
            with _KeepAwake():
                job.result = _finite(json.loads(json.dumps(self.handlers[job.type](ctx, job.params), default=_plain)))
            job.state, job.progress = "done", 1.0
            job.message = (job.result or {}).get("message", "Done.") if isinstance(job.result, dict) else "Done."
        except Cancelled:
            job.state, job.message = "cancelled", "Cancelled. Press Resume to continue later."
        except JobError as e:
            job.state, job.error, job.message = "failed", str(e), str(e)
            ctx.log("FAILED " + str(e))
        except Exception as e:  # unexpected: keep the details in the log, show something readable
            if job.cancel_flag.is_set():
                job.state, job.message = "cancelled", "Cancelled. Press Resume to continue later."
            else:
                job.state, job.error = "failed", f"{type(e).__name__}: {e}"
                job.message = "Something went wrong: " + job.error
                ctx.log(traceback.format_exc())
        finally:
            job.proc = None
            job.finished = time.time()
            self.changed(job)
