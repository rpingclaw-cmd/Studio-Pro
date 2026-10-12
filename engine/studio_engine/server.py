"""The engine's small web server on 127.0.0.1 (this computer only). The Studio tab talks to it."""
import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from . import VERSION, audio, config, faces, hardware, matte, media, render, syncer, thumbs, transcribe, vad
from .jobs import JobError, JobManager

HANDLERS = {
    "report": media.job_report,
    "prepare": media.job_prepare,
    "sync": syncer.job_sync,
    "clean": audio.job_clean,
    "preview": audio.job_preview,
    "render": render.job_render,
    "analyse": vad.job_analyse,
    "faces": faces.job_faces,
    "frame": render.job_frame,
    "thumbs": thumbs.job_thumbs,
    "transcribe": transcribe.job_transcribe,
    "matte": matte.job_matte,
}

app = FastAPI(title="Studio Engine", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
mgr: JobManager | None = None
TOKEN = ""


def manager() -> JobManager:
    global mgr
    if mgr is None:
        mgr = JobManager(HANDLERS)
    return mgr


# ---------------- security: this computer only, our site only, paired browsers only ----------------
@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return PlainTextResponse("Forbidden host", status_code=403)  # blocks DNS-rebinding tricks
    path = request.url.path
    if request.method != "OPTIONS" and path.startswith("/api/") and path != "/api/hello":
        given = request.headers.get("x-engine-token") or request.query_params.get("t") or ""
        if not TOKEN or given != TOKEN:
            return JSONResponse({"error": "not paired"}, status_code=401)
    return await call_next(request)


app.add_middleware(CORSMiddleware, allow_origin_regex=config.ALLOWED_ORIGIN_RE, allow_methods=["*"],
                   allow_headers=["*"], allow_credentials=False, max_age=600)


@app.middleware("http")
async def private_network(request: Request, call_next):
    """Chrome asks before a website may talk to this computer; answer 'yes' for our allowed site."""
    resp = await call_next(request)
    if request.headers.get("access-control-request-private-network"):
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


@app.exception_handler(JobError)
async def job_error(_req, exc: JobError):
    return JSONResponse({"error": str(exc)}, status_code=400)


# ---------------- endpoints ----------------
@app.get("/api/hello")
def hello(request: Request):
    given = request.headers.get("x-engine-token") or request.query_params.get("t") or ""
    return {"app": "studio-engine", "version": VERSION, "paired": bool(TOKEN) and given == TOKEN}


@app.get("/api/hardware")
def get_hardware(force: int = 0):
    return hardware.report(force=bool(force))


@app.post("/api/pick")
def pick(body: dict = Body(default={})):
    kind = str(body.get("kind") or "any")
    cmd = [sys.executable, "-m", "studio_engine.picker", kind]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8",
                           creationflags=0x08000000 if config.IS_WIN else 0)
    except subprocess.TimeoutExpired:
        return {"path": ""}
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    path = lines[-1].strip() if lines else ""
    return {"path": str(Path(path).resolve()) if path else ""}


@app.post("/api/check-path")
def check_path(body: dict = Body(...)):
    p = Path(str(body.get("path") or "").strip().strip('"')).expanduser()
    return {"path": str(p.resolve()) if p.is_file() else "", "exists": p.is_file(),
            "size_gb": round(p.stat().st_size / 2**30, 2) if p.is_file() else 0}


@app.get("/api/jobs")
def list_jobs():
    return manager().list()


@app.post("/api/jobs")
def new_job(body: dict = Body(...)):
    t = body.get("type")
    if t not in HANDLERS:
        raise HTTPException(400, f"unknown job type {t}")
    return manager().submit(t, body.get("params") or {}).public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id not in manager().jobs:
        raise HTTPException(404)
    manager().cancel(job_id)
    return manager().jobs[job_id].public()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    if job_id not in manager().jobs:
        raise HTTPException(404)
    return manager().resume(job_id).public()


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str):
    j = manager().jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    try:
        return PlainTextResponse(manager().log_path(j).read_text(encoding="utf-8")[-200000:])
    except OSError:
        return PlainTextResponse("\n".join(j.log_lines))


@app.get("/api/events")
async def events(request: Request):
    """Server-Sent Events: the job list every time something changes (progress, finished, …)."""
    m = manager()

    async def gen():
        last = -1
        ping = time.time()
        while not await request.is_disconnected():
            if m.version != last:
                last = m.version
                yield f"data: {json.dumps(m.list()[:30])}\n\n"
                ping = time.time()
            elif time.time() - ping > 15:
                yield ": ping\n\n"
                ping = time.time()
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _allowed_file(p: Path) -> bool:
    """Only files the engine made (inside a studio_work folder) or listed as job results may be read."""
    if "studio_work" in p.parts:
        return True
    for j in manager().jobs.values():
        r = j.result if isinstance(j.result, dict) else {}
        if str(p) in [str(Path(x).resolve()) for x in r.get("outputs", [])]:
            return True
    return False


TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".json": "application/json",
         ".srt": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


@app.get("/api/file")
def get_file(request: Request, path: str):
    p = Path(path).resolve()
    if not p.is_file() or not _allowed_file(p):
        raise HTTPException(404, "not available")
    size = p.stat().st_size
    ctype = TYPES.get(p.suffix.lower(), "application/octet-stream")
    rng = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if rng and rng.startswith("bytes="):
        a, _, b = rng[6:].split(",")[0].partition("-")
        try:
            if a:
                start, end = int(a), (int(b) if b else size - 1)
            else:
                start, end = size - int(b), size - 1
        except ValueError:
            raise HTTPException(416)
        end = min(end, size - 1)
        if start > end or start < 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    def body(s=start, e=end):
        with open(p, "rb") as f:
            f.seek(s)
            left = e - s + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "no-cache"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=ctype, headers=headers)


@app.post("/api/open-folder")
def open_folder(body: dict = Body(...)):
    p = Path(str(body.get("path") or "")).resolve()
    if not p.exists():
        raise HTTPException(404)
    try:
        if config.IS_WIN:
            subprocess.Popen(["explorer", "/select,", str(p)] if p.is_file() else ["explorer", str(p)])
        elif config.IS_MAC:
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent if p.is_file() else p)])
    except OSError:
        pass
    return {"ok": True}


# ---------------- the app's own pages ----------------
@app.get("/")
def index():
    f = config.UI_DIR / "index.html"
    if not f.is_file():
        raise HTTPException(404, "no app pages here")
    return Response(f.read_bytes(), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


UI_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
            ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}


@app.get("/{sub:path}")
def ui_file(sub: str):
    base = config.UI_DIR.resolve()
    f = (base / sub).resolve()
    if base not in f.parents or not f.is_file() or f.suffix.lower() not in UI_TYPES:
        raise HTTPException(404)
    return Response(f.read_bytes(), media_type=UI_TYPES[f.suffix.lower()], headers={"Cache-Control": "no-cache"})


# ---------------- start ----------------
def find_chrome() -> list[str] | None:
    """Google Chrome, if installed (the owner prefers it; it also plays the preview video)."""
    if config.IS_WIN:
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for r in roots:
            exe = Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if r and exe.is_file():
                return [str(exe)]
        return None
    if config.IS_MAC:
        app = Path("/Applications/Google Chrome.app")
        return ["open", "-a", "Google Chrome"] if app.exists() else None
    import shutil
    exe = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    return [exe] if exe else None


def open_site(link: str):
    """Open the website in Google Chrome when it is installed, otherwise in the default browser.
    (settings.json "browser": "default" turns the Chrome preference off.)"""
    if config.load_settings().get("browser") != "default":
        chrome = find_chrome()
        if chrome:
            try:
                subprocess.Popen(chrome + [link], creationflags=0x08000000 if config.IS_WIN else 0)
                return
            except OSError:
                pass
    webbrowser.open(link)


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((config.HOST, port)) == 0


def main():
    global TOKEN
    ap = argparse.ArgumentParser(description="Studio Engine")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open the website")
    ap.add_argument("--site", default=config.SITE)
    ap.add_argument("--app", action="store_true", help="started by the Studio Pro app: no browser, stop when the app stops")
    args = ap.parse_args()
    TOKEN = config.token()
    link = f"{args.site}/#engine={TOKEN}"
    if args.app:
        args.no_browser = True
        import threading

        def watch_parent():  # the app keeps our input open; when it closes (even if it crashed), stop
            try:
                while sys.stdin.read(1024):
                    pass
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=watch_parent, daemon=True).start()
    if _port_busy(args.port) and not args.app:
        print("Studio Engine is already running. Opening the website…")
        if not args.no_browser:
            open_site(link)
        return
    if args.app:
        print(f"Studio Engine {VERSION} running for Studio Pro on 127.0.0.1:{args.port}", flush=True)
    else:
        print("=" * 64)
        print(f"  Studio Engine {VERSION} is running.  Keep this window open while you work.")
        print("  Close this window to stop it.")
        print(f"  If the Studio tab says 'not paired', open this link:\n  {link}")
        print("=" * 64, flush=True)
    manager()
    if not args.no_browser:
        open_site(link)
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=args.port, log_level="warning", timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
