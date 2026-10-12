"""Where the engine keeps its own files, the pairing token, and how it finds ffmpeg / deep-filter."""
import json
import os
import platform
import secrets
import shutil
import sys
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("STUDIO_ENGINE_PORT", "8765"))
SITE = "https://psm.dailyquotecards.workers.dev"
# the app's own pages (served by the engine itself): ../ui next to the engine, or STUDIO_UI_DIR
UI_DIR = Path(os.environ.get("STUDIO_UI_DIR") or Path(__file__).resolve().parents[2] / "ui")
# Only the live site and local test copies may talk to the engine.
ALLOWED_ORIGIN_RE = r"^(https://psm\.dailyquotecards\.workers\.dev|http://(localhost|127\.0\.0\.1)(:\d+)?)$"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def app_dir() -> Path:
    if os.environ.get("STUDIO_ENGINE_HOME"):
        d = Path(os.environ["STUDIO_ENGINE_HOME"])
    elif IS_WIN:
        d = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "StudioEngine"
    elif IS_MAC:
        d = Path.home() / "Library" / "Application Support" / "StudioEngine"
    else:
        d = Path.home() / ".studio-engine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_file() -> Path:
    return app_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    tmp = _settings_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_file())


def token() -> str:
    """Random pairing token. It stays the same across restarts so the browser stays paired."""
    if os.environ.get("STUDIO_ENGINE_TOKEN"):  # given by the Studio Pro app for this run
        return os.environ["STUDIO_ENGINE_TOKEN"]
    s = load_settings()
    if not s.get("token"):
        s["token"] = secrets.token_urlsafe(24)
        save_settings(s)
    return s["token"]


def _exe(name: str) -> str:
    return name + (".exe" if IS_WIN else "")


def find_tool(name: str) -> str | None:
    """ffmpeg / ffprobe / deep-filter: our own bin folder first, then the usual install places, then PATH."""
    env = os.environ.get("STUDIO_" + name.upper().replace("-", "_"))
    if env and Path(env).exists():
        return env
    candidates = [app_dir() / "bin" / _exe(name)]
    if os.environ.get("STUDIO_BIN"):  # the tools that come with the Studio Pro app
        candidates.insert(0, Path(os.environ["STUDIO_BIN"]) / _exe(name))
    if IS_WIN:
        la = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates.append(la / "Microsoft" / "WinGet" / "Links" / _exe(name))
        pkgs = la / "Microsoft" / "WinGet" / "Packages"
        if pkgs.is_dir():
            candidates += sorted(pkgs.glob(f"Gyan.FFmpeg*/**/bin/{_exe(name)}"))
    if IS_MAC:
        candidates += [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which(name)


def machine() -> dict:
    return {"os": platform.system(), "os_version": platform.release(), "arch": platform.machine(),
            "python": platform.python_version()}
