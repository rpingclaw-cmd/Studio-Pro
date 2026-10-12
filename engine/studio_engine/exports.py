"""Stage 4 exports: a timeline for DaVinci Resolve (FCPXML + EDL), WebVTT captions, and caption checks."""
import re
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape


def _rt(frames: int, fr: Fraction) -> str:
    """FCPXML rational time: frames at the given frame rate, e.g. '3003/30000s'."""
    t = Fraction(frames) / fr
    return f"{t.numerator}/{t.denominator}s" if t.denominator != 1 else f"{t.numerator}s"


def _file_url(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if not s.startswith("/"):
        s = "/" + s  # Windows: file:///C:/...
    return "file://" + quote(s, safe="/:")


def fcpxml(video: Path, clean: Path | None, segs: list, fr: Fraction, src_w: int, src_h: int, src_frames: int,
           out_w: int, out_h: int, name: str = "Edited talk", chapters: list | None = None) -> str:
    """Final Cut Pro XML 1.9 (DaVinci Resolve: File > Import > Timeline). Every kept part is a clip of the
    original video; the cleaned sound (already in sync with the video) is attached under each clip and the
    camera sound is muted."""
    total = sum(b - a for a, b in segs)
    fd = _rt(1, fr)
    # chapter markers belong to the clip that contains them, timed in that clip's own (source) clock
    marks: dict[int, list[str]] = {}
    for c in chapters or []:
        t, off = int(round(c["t"] * fr)), 0
        for i, (a, b) in enumerate(segs):
            if off <= t < off + (b - a) or i == len(segs) - 1:
                pos = a + min(max(0, t - off), b - a - 1)
                marks.setdefault(i, []).append(f'<marker start="{_rt(pos, fr)}" duration="{fd}" value="{escape(c["title"])}"/>')
                break
            off += b - a
    clips = []
    off = 0
    for i, (a, b) in enumerate(segs):
        n = b - a
        audio = (f'<asset-clip ref="r4" lane="-1" offset="{_rt(a, fr)}" start="{_rt(a, fr)}" duration="{_rt(n, fr)}" '
                 f'name="Cleaned sound" audioRole="dialogue"/>' if clean else "")
        clips.append(f'      <asset-clip ref="r3" offset="{_rt(off, fr)}" start="{_rt(a, fr)}" duration="{_rt(n, fr)}" '
                     f'name="Part {i + 1}" tcFormat="NDF"><adjust-volume amount="-96dB"/>{audio}'
                     f'{"".join(marks.get(i, []))}</asset-clip>')
        off += n
    clean_asset = (f'    <asset id="r4" name="{escape(clean.stem)}" start="0s" duration="{_rt(src_frames, fr)}" hasAudio="1" '
                   f'audioSources="1" audioChannels="2" audioRate="48000"><media-rep kind="original-media" '
                   f'src="{_file_url(clean)}"/></asset>\n') if clean else ""
    spine = "\n".join(clips)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="Timeline" frameDuration="{fd}" width="{out_w}" height="{out_h}"/>
    <format id="r2" name="Source" frameDuration="{fd}" width="{src_w}" height="{src_h}"/>
    <asset id="r3" name="{escape(video.stem)}" start="0s" duration="{_rt(src_frames, fr)}" hasVideo="1" hasAudio="1" format="r2" audioSources="1" audioChannels="2" audioRate="48000"><media-rep kind="original-media" src="{_file_url(video)}"/></asset>
{clean_asset}  </resources>
  <library>
    <event name="Studio">
      <project name="{escape(name)}">
        <sequence format="r1" duration="{_rt(total, fr)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
{spine}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def parse_chapters(text: str) -> list[dict]:
    """'0:00 Intro' / '1:02:05 Title' lines -> [{'t': seconds, 'title': ...}]"""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*((?:\d+:)?\d+:\d{2})\s+(.+)", line)
        if m:
            t = sum(int(x) * 60 ** i for i, x in enumerate(reversed(m.group(1).split(":"))))
            out.append({"t": float(t), "title": m.group(2).strip()})
    return out


def _tc(frames: int, fps: int) -> str:
    f = frames % fps
    s = frames // fps
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}:{f:02d}"


def edl(video: Path, segs: list, fr: Fraction, title: str = "EDITED TALK") -> str:
    """CMX 3600 cut list (video + audio), source timecode from 00:00:00:00."""
    fps = int(round(float(fr)))
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec = 0
    reel = re.sub(r"[^A-Za-z0-9]", "", video.stem)[:8].upper() or "AX"
    for i, (a, b) in enumerate(segs):
        n = b - a
        lines.append(f"{i + 1:03d}  {reel:<8} AA/V  C        {_tc(a, fps)} {_tc(b, fps)} {_tc(rec, fps)} {_tc(rec + n, fps)}")
        lines.append(f"* FROM CLIP NAME: {video.name}")
        lines.append("")
        rec += n
    return "\n".join(lines) + "\n"


def srt_cues(srt: str) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = block[m.end():].strip()
        out.append({"s": s, "e": e, "text": text})
    return out


def vtt_from_srt(srt: str) -> str:
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}.{ms % 1000:03d}"
    cues = srt_cues(srt)
    return "WEBVTT\n\n" + "\n\n".join(f"{ts(c['s'])} --> {ts(c['e'])}\n{c['text']}" for c in cues) + "\n"


def caption_check(srt: str, video_s: float) -> dict:
    """Readability rules used by broadcasters: no overlaps, at least 0.7 s on screen, at most 42 characters per
    line and 2 lines, reading speed at most 20 characters per second, nothing after the end of the video."""
    cues = srt_cues(srt)
    issues = []
    for i, c in enumerate(cues):
        d = c["e"] - c["s"]
        lines = c["text"].splitlines() or [""]
        chars = len(" ".join(lines))
        if d < 0.7:
            issues.append((c["s"], f"on screen only {d:.2f} s"))
        if max(len(l) for l in lines) > 42 or len(lines) > 2:
            issues.append((c["s"], "line too long"))
        if d > 0 and chars / d > 20:
            issues.append((c["s"], f"fast to read ({chars / d:.0f} characters per second)"))
        if i and c["s"] < cues[i - 1]["e"] - 0.001:
            issues.append((c["s"], "overlaps the previous caption"))
        if c["e"] > video_s + 0.5:
            issues.append((c["s"], "after the end of the video"))
    return {"cues": len(cues), "issues": [{"t": round(t, 2), "text": x} for t, x in issues]}
