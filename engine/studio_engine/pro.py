"""Pro tools for the finished video: titles & lower thirds, background music that dips under the voice,
extra copies (WhatsApp, square), the owner's LUT. Used by render.py."""
import hashlib
import re
import shutil
from fractions import Fraction
from pathlib import Path

from . import audio, ff, media
from .jobs import Ctx, JobError

FONTS = Path(__file__).resolve().parent / "fonts"  # DejaVu Sans (free licence): titles look the same everywhere
_T = re.compile(r"^Dialogue:\s*\d+,(\d+):(\d+):(\d+(?:\.\d+)?),(\d+):(\d+):(\d+(?:\.\d+)?),")


# ---------- titles (an ASS file made by the editor, on the edited timeline) ----------
def split_ass(text: str) -> tuple[str, list[tuple[float, float, str]]]:
    head, events = [], []
    for line in (text or "").splitlines():
        m = _T.match(line)
        if m:
            g = m.groups()
            s = int(g[0]) * 3600 + int(g[1]) * 60 + float(g[2])
            e = int(g[3]) * 3600 + int(g[4]) * 60 + float(g[5])
            events.append((s, e, line))
        elif not line.startswith("Dialogue:"):
            head.append(line)
    return "\n".join(head).rstrip() + "\n", events


def titles_filter(d: Path, ass: str, t0: float, t1: float) -> str:
    """Filter that draws the titles shown between output seconds t0..t1 onto a piece starting at t0 ('' if none).
    The piece file depends only on those titles, so changing one title re-makes only the parts it touches."""
    head, events = split_ass(ass)
    mine = [ln for s, e, ln in events if s < t1 and e > t0]
    if not mine:
        return ""
    body = head + "\n".join(mine) + "\n"
    name = f"titles_{hashlib.sha1(body.encode()).hexdigest()[:12]}.ass"
    (d / name).write_text(body, encoding="utf-8")
    if not (d / "fonts").is_dir():
        shutil.copytree(FONTS, d / "fonts")
    return f"setpts=PTS+{t0:.6f}/TB,subtitles=filename={name}:fontsdir=fonts,setpts=PTS-{t0:.6f}/TB"


def add_to_filter(vf: str, extra: str, complex_graph: bool) -> str:
    if not extra:
        return vf
    if complex_graph:  # ...[vout]  ->  ...,extra[vout]
        assert vf.endswith("[vout]")
        return vf[:-6] + "," + extra + "[vout]"
    return f"{vf},{extra}"


# ---------- background music ----------
def mix_music(ctx: Ctx, voice: Path, music: dict, out: Path, dur: float):
    """Music under the voice: looped to the length, its own level, fades, and 'ducking' (it dips while someone
    speaks, comes back up in the pauses). A true-peak limiter keeps the loudness target safe."""
    src = Path(str(music.get("file") or ""))
    if not src.is_file():
        raise JobError("The background music file was not found — choose it again (Fairlight page → Music).")
    db = max(-40.0, min(0.0, float(music.get("db", -18))))
    fi, fo = max(0.0, float(music.get("fade_in", 2))), max(0.0, float(music.get("fade_out", 3)))
    fo = min(fo, dur / 2)
    m = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={db:.1f}dB,atrim=end={dur:.6f},"
         f"afade=t=in:d={fi:.2f},afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.2f}")
    if music.get("duck", True):
        amount = max(2.0, min(30.0, float(music.get("duck_db", 12))))
        ratio = 1.0 + amount / 3.0
        graph = (f"{m}[m];[0:a]asplit=2[v][sc];[m][sc]sidechaincompress=threshold=0.015:ratio={ratio:.1f}:attack=40:"
                 f"release=600:makeup=1[md];[v][md]amix=inputs=2:normalize=0:duration=first,{audio.limiter()}[out]")
    else:
        graph = f"{m}[m];[0:a][m]amix=inputs=2:normalize=0:duration=first,{audio.limiter()}[out]"
    tmp = ff.tmp_of(out)
    ff.run_ff(ctx, ["-i", voice, "-stream_loop", "-1", "-i", src, "-filter_complex", graph, "-map", "[out]",
                    "-c:a", "pcm_s16le", "-ar", "48000", tmp], duration=dur, msg="Mixing the background music…")
    ff.finish(tmp, out)


def relevel(ctx: Ctx, wav: Path, target: float, dur: float, out: Path) -> dict:
    """After music or per-clip voice levels the loudness has moved: measure and set it to the target again
    (exact gain + true-peak limiter, a few passes), keeping the length and timing exact."""
    src = wav
    lv = {}
    for _ in range(3):
        lv = audio.parse_ebur(ff.run_ff(ctx, ["-i", src, "-af", "ebur128=peak=true", "-f", "null", "-"], duration=dur,
                                        msg="Setting the loudness…", loglevel="info", want_stderr=True))
        if abs(lv["lufs"] - target) <= 0.3 and lv["true_peak"] <= -1.0 and src != wav:
            break
        tmp = ff.tmp_of(out)
        ff.run_ff(ctx, ["-i", src, "-af", audio.loud_args(target, dur, target - lv["lufs"]), "-c:a", "pcm_s16le",
                        "-ar", "48000", tmp], duration=dur, msg="Setting the loudness…")
        nxt = out.with_name(out.stem + f"_{_}.wav")
        ff.finish(tmp, nxt)
        src = nxt
    shutil.copyfile(src, out)
    return lv


# ---------- extra copies ----------
EXTRAS = {
    "whatsapp": ("Edited - full video (WhatsApp).mp4", "scale=-2:'min(720,ih)':flags=lanczos,format=yuv420p",
                 ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k"]),
    "square": ("Edited - full video (square).mp4", "crop='min(iw,ih)':'min(iw,ih)',scale=1080:1080:flags=lanczos,format=yuv420p",
               ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k"]),
}


def extra_copies(ctx: Ctx, final: Path, which: list, dur: float) -> list[Path]:
    out = []
    for k in which:
        if k not in EXTRAS:
            continue
        name, vf, codec = EXTRAS[k]
        dst = final.with_name(name)
        tmp = ff.tmp_of(dst)
        ff.run_ff(ctx, ["-i", final, "-vf", vf, *codec, "-movflags", "+faststart", tmp], duration=dur,
                  msg=f"Making the {k} copy…")
        ff.finish(tmp, dst)
        out.append(dst)
    return out


# ---------- the owner's LUT (.cube) ----------
def lut_file(video, colour: dict) -> str | None:
    src = Path(str((colour or {}).get("lut") or ""))
    if not (colour or {}).get("lut"):
        return None
    if not src.is_file() or src.suffix.lower() not in (".cube", ".3dl"):
        raise JobError("The look (LUT) file was not found or is not a .cube file — choose it again (Color page).")
    data = src.read_bytes()
    d = media.work_dir(str(video)) / "looks"
    d.mkdir(exist_ok=True)
    dst = d / f"look_{hashlib.sha1(data).hexdigest()[:12]}{src.suffix.lower()}"
    if not dst.exists():
        dst.write_bytes(data)
    return str(dst)


def voice_gains(p: dict, segs, fr: Fraction) -> list[float]:
    """Voice level per kept part (dB), from the editor's per-clip levels (source seconds)."""
    ranges = [(float(g["start"]), float(g["end"]), float(g.get("db", 0))) for g in (p.get("voice_gains") or [])]
    out = []
    for a, b in segs:
        mid = float(Fraction(a + b, 2) / fr)
        out.append(next((db for s, e, db in ranges if s <= mid < e), 0.0))
    return out
