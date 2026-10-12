"""What this computer can do: processor, memory, graphics, and which fast video encoders really work."""
import os
import re
import shutil
import subprocess

from . import config

NOWIN = 0x08000000 if config.IS_WIN else 0


def _run(cmd, timeout=20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=NOWIN)
        return r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def ram_gb() -> float:
    try:
        if config.IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30, 1)
        if config.IS_MAC:
            return round(int(_run(["sysctl", "-n", "hw.memsize"]).strip()) / 2**30, 1)
        with open("/proc/meminfo") as f:
            return round(int(f.readline().split()[1]) / 2**20, 1)
    except Exception:
        return 0.0


def cpu_name() -> str:
    if config.IS_WIN:
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        return out.strip().splitlines()[0] if out.strip() else ""
    if config.IS_MAC:
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def gpus() -> list[str]:
    if config.IS_WIN:
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
        return [l.strip() for l in out.splitlines() if l.strip()]
    if config.IS_MAC:
        out = _run(["system_profiler", "SPDisplaysDataType"])
        return [m.strip() for m in re.findall(r"Chipset Model:\s*(.+)", out)]
    out = _run(["sh", "-c", "lspci 2>/dev/null | grep -iE 'vga|3d|display'"])
    return [l.split(":", 2)[-1].strip() for l in out.splitlines() if l.strip()]


ENCODERS = [  # (ffmpeg encoder, plain name)
    ("h264_videotoolbox", "Apple VideoToolbox"),
    ("h264_nvenc", "NVIDIA NVENC"),
    ("h264_qsv", "Intel Quick Sync"),
    ("h264_amf", "AMD AMF"),
]


def encoder_works(ffmpeg: str, enc: str) -> bool:
    out = _run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=30:d=0.5",
                "-pix_fmt", "yuv420p", "-c:v", enc, "-f", "null", "-"], timeout=30)
    return out.strip() == ""


def _tools_sig() -> list:
    """Which ffmpeg / deep-filter are in use (path, size, date): the check is redone when they change."""
    out = []
    for n in ("ffmpeg", "deep-filter"):
        t = config.find_tool(n)
        try:
            st = os.stat(t) if t else None
        except OSError:
            st = None
        out.append([t, st.st_size if st else 0, int(st.st_mtime) if st else 0])
    return out


def report(force: bool = False) -> dict:
    s = config.load_settings()
    sig = _tools_sig()
    if s.get("hardware") and not force and s["hardware"].get("_sig") == sig:
        hw = s["hardware"]
    else:
        ffmpeg = config.find_tool("ffmpeg")
        hw = {"cpu": cpu_name(), "cores": os.cpu_count() or 1, "ram_gb": ram_gb(), "gpus": gpus(),
              "ffmpeg": None, "encoders": {}, **config.machine()}
        if ffmpeg:
            v = _run([ffmpeg, "-hide_banner", "-version"]).splitlines()
            hw["ffmpeg"] = v[0] if v else "ffmpeg"
            listed = _run([ffmpeg, "-hide_banner", "-encoders"])
            for enc, _ in ENCODERS:
                hw["encoders"][enc] = (enc in listed) and encoder_works(ffmpeg, enc)
            filters = _run([ffmpeg, "-hide_banner", "-filters"])
            hw["zscale"] = " zscale " in filters
        df = config.find_tool("deep-filter")
        hw["deep_filter"] = (_run([df, "--version"]).strip() or "found") if df else None
        hw["_sig"] = sig
        s["hardware"] = hw
        config.save_settings(s)
    best = "libx264"
    for enc, name in ENCODERS:
        if hw.get("encoders", {}).get(enc):
            best = enc
            break
    hw["best_encoder"] = best
    hw["best_encoder_name"] = dict(ENCODERS).get(best, "Processor (x264)")
    hw["summary"] = summary(hw)
    return hw


def summary(hw: dict) -> list[str]:
    lines = [f"{hw.get('cpu') or 'Processor'} · {hw.get('cores')} threads · {hw.get('ram_gb')} GB memory"]
    if hw.get("gpus"):
        lines.append("Graphics: " + ", ".join(hw["gpus"]))
    if not hw.get("ffmpeg"):
        lines.append("⚠ ffmpeg not found — run the installer again.")
    else:
        fast = [n for e, n in ENCODERS if hw.get("encoders", {}).get(e)]
        lines.append("Fast video encoding: " + (", ".join(fast) if fast else "not available — the processor will do it (slower)."))
    lines.append("Voice isolation (DeepFilterNet): " + ("ready ✓" if hw.get("deep_filter") else "⚠ not installed"))
    return lines


def free_gb(path) -> float:
    try:
        return round(shutil.disk_usage(path).free / 2**30, 1)
    except OSError:
        return -1.0


def disk_check(folder, need_gb: float, what: str):
    from .jobs import JobError
    free = free_gb(folder)
    if 0 <= free < need_gb:
        raise JobError(f"Not enough free disk space for {what}: about {need_gb:.1f} GB needed, "
                       f"only {free:.1f} GB free on that drive. Free some space and press Resume.")
