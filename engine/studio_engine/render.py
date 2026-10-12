"""Render the edit on this computer, then check the result (lip sync at 3 points, loudness, length).

Lip sync stays exact because every cut is snapped to a whole video frame, the picture parts are encoded
without sound, and the sound is assembled once, sample-exact, from the cleaned audio and added at the end.
"""
import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import audio, config, exports, faces, ff, framing, hardware, matte, media, picture, pro, syncer
from .jobs import Ctx, JobError, file_sig, key_of, mark_done, read_done, step_done
from .wavio import Writer, open_pcm16

FADE_S = 0.005


def frames_of(keep: list, fr: Fraction, total_frames: int, lo: int = 0) -> list[tuple[int, int]]:
    """Kept parts (seconds) -> frame ranges between lo (negative: before the camera started, when the whole
    recording is kept) and total_frames (which then includes the time after the camera stopped)."""
    out = []
    for s, e in keep:
        a = max(lo, int(round(float(s) * fr)))
        b = min(total_frames, int(round(float(e) * fr)))
        if b - a < 2:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def vcodec(enc: str, kind: str, height: int) -> list[str]:
    full = kind == "full"
    if enc == "h264_videotoolbox":
        rate = "40M" if height > 2000 else "20M" if height > 1500 else "12M"
        return ["-c:v", enc, "-b:v", rate, "-allow_sw", "1"]
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", "20" if full else "19", "-b:v", "0"]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-preset", "medium", "-global_quality", "22" if full else "21"]
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"]
    return ["-c:v", "libx264", "-preset", "veryfast" if full else "medium", "-crf", "20" if full else "19"]


COLOR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
OUT_SIZES = {"1080": (1920, 1080), "1440": (2560, 1440), "2160": (3840, 2160)}


def geometry(v: dict, kind: str, st: dict, reel_h: int) -> dict:
    """Output size and shape. 'crop' = a window that follows the face (also used for the original shape,
    where it is the whole picture except during punch-ins); 'blur' = 16:9 with blurred sides."""
    W, H = int(v.get("width") or 1920), int(v.get("height") or 1080)
    if abs(media.rotation(v)) % 180 == 90:
        W, H = H, W
    res = str(st.get("res") or "1080")
    layout = st.get("layout") or "original"
    if kind == "reel":
        return {"W": W, "H": H, "OW": reel_h * 9 // 16, "OH": reel_h, "aspect": 9 / 16, "mode": "crop",
                "punch": float(st.get("reelPunch", 1.12))}
    if layout in ("crop169", "blur169"):
        if res in OUT_SIZES:
            OW, OH = OUT_SIZES[res]
        else:  # 'orig': the biggest 16:9 window of the source
            bw = W if W / H < 16 / 9 else H * 16 / 9
            OW, OH = int(bw) // 2 * 2, int(bw * 9 / 16) // 2 * 2
        return {"W": W, "H": H, "OW": OW, "OH": OH, "aspect": 16 / 9,
                "mode": "blur" if layout == "blur169" else "crop", "punch": float(st.get("punch", 1.18))}
    if res == "orig":
        OW, OH = W // 2 * 2, H // 2 * 2
    else:
        n = int(res)
        OW, OH = (int(round(W * n / H / 2)) * 2, n) if W > H else (n, int(round(H * n / W / 2)) * 2)
    return {"W": W, "H": H, "OW": OW, "OH": OH, "aspect": W / H, "mode": "crop", "punch": float(st.get("punch", 1.0))}


def look(v: dict, st: dict, gains, lut: str | None = None) -> str:
    """After framing: HDR -> normal screens, colour (incl. colour wheels), the owner's LUT, pixel format."""
    f = [x for x in (media.tonemap_chain(v), picture.colour_filter(st, gains)) if x]
    if lut:
        f.append(f"lut3d=file={lut}:interp=tetrahedral")
    f.append("format=yuv420p")
    return ",".join(f)


class Pictures:
    """Builds the ffmpeg picture filter for any stretch of the source, with framing, punch-in and colour."""

    def __init__(self, v, kind, st, reel_h, faces_d, segs, fr):
        self.v, self.st, self.fr, self.segs, self.kind = v, st, fr, segs, kind
        self.g = geometry(v, kind, st, reel_h)
        self.cwd = None  # folder ffmpeg runs in (files in the filter are given relative to it)
        self.n_calls = 0
        bg = st.get("background") or {}
        self.bg = bg if bg.get("mode") in ("blur", "colour", "image") and st.get("_matte") else None
        if self.bg:
            mi = media.stream(ff.probe(st["_matte"]), "video")
            self.matte_wh = (int(mi["width"]), int(mi["height"]))
        g = self.g
        self.framer = framing.Framer(faces_d, g["W"], g["H"], g["aspect"])
        self.segs_s = [(float(Fraction(a) / fr), float(Fraction(b) / fr)) for a, b in segs]
        self.zooms = framing.part_zooms(len(segs), g["punch"], st.get("framing_overrides") or [], self.segs_s)
        auto = (st.get("colour") or {}).get("auto", True)
        self.gains = [picture.colour_gains(faces_d, a, b) if auto else None for a, b in self.segs_s]

    def vf(self, src: int, n: int, part: int, cmd_name: str | None) -> tuple[str, str | None]:
        """Filter for source frames src..src+n (kept part `part`, or -1 for borrowed footage).
        Returns (filter, sendcmd text or None)."""
        g, fr = self.g, self.fr
        times = np.array([float(Fraction(src + i) / fr) for i in range(n)])
        gains = self.gains[part] if part >= 0 else None
        a, b = self.segs_s[part] if part >= 0 else (times[0], times[-1])
        ov = self.st.get("framing_overrides") or []
        fy = framing.override_y(ov, a, b, self.framer)
        if g["mode"] == "blur":
            mid = np.array([times[len(times) // 2]])
            _, ch, _, yy = self.framer.at(mid, 1.0, fy)
            bg_h = int(round(g["OW"] * g["H"] / g["W"] / 2)) * 2
            cy = (yy[0] + framing.LEAD * ch) / g["H"]  # face height in the picture (0..1)
            y = int(min(max(0, cy * bg_h - g["OH"] / 2), max(0, bg_h - g["OH"])))
            col = ",".join(x for x in (picture.colour_filter(self.st, gains),
                                       f"lut3d=file={self.rel(self.st['_lut'])}" if self.st.get("_lut") else "") if x)
            return (f"scale=-2:{g['OH']}:flags=lanczos,{media.tonemap_chain(self.v) or 'null'},split=2[a][b];"
                    f"[a]scale={g['OW']}:{bg_h},crop={g['OW']}:{g['OH']}:0:{y},boxblur=luma_radius=30:luma_power=2,"
                    f"eq=brightness=-0.06[bg];[bg][b]overlay=(W-w)/2:0" + (f",{col}" if col else "") + ",format=yuv420p",
                    None)
        z = self.zooms[part] if part >= 0 else 1.0
        rect = self.rect_for(a, b) if self.kind != "reel" else None
        if rect:
            cw, ch, x, y = self.fixed(rect, len(times))
        else:
            cw, ch, x, y = self.framer.at(times, z, fy)
        ww = hh = None
        kb = self.kb_for(a, b) if part >= 0 else 0.0
        if kb:  # slow zoom ("Ken Burns") through this part: the window shrinks a little, frame by frame
            p0, plen = self.segs[part][0], max(1, self.segs[part][1] - self.segs[part][0])
            zz = 1 + kb * np.clip((np.arange(n) + src - p0) / plen, 0, 1)
            ww = (np.round(cw / zz / 2) * 2).astype(int)
            hh = (np.round(ch / zz / 2) * 2).astype(int)
            x, y = x + (cw - ww) // 2, y + (ch - hh) // 2
            cw, ch = int(ww[0]), int(hh[0])
        moving = bool((x != x[0]).any() or (y != y[0]).any() or (ww is not None and (ww != ww[0]).any()))
        crop = f"crop@fr=w={cw}:h={ch}:x={x[0]}:y={y[0]}"
        scale = f"scale={g['OW']}:{g['OH']}:flags=lanczos"
        lk = look(self.v, self.st, gains, self.rel(self.st["_lut"]) if self.st.get("_lut") else None)
        cmd = framing.sendcmd_text(fr, x, y, w=ww, h=hh) if moving and cmd_name else None
        pre = f"sendcmd=f={cmd_name}," if cmd else ""
        if not self.bg:
            return f"{pre}{crop},{scale},{lk}", cmd
        # background replacement: the person (from the matte) over blur / a colour / a picture
        mw, mh = self.matte_wh
        sx, sy = mw / g["W"], mh / g["H"]
        mx, my = np.round(x * sx).astype(int), np.round(y * sy).astype(int)
        mcw, mch = max(2, int(round(cw * sx))), max(2, int(round(ch * sy)))
        if cmd:
            mww = None if ww is None else np.maximum(2, np.round(ww * sx).astype(int))
            mhh = None if hh is None else np.maximum(2, np.round(hh * sy).astype(int))
            cmd += framing.sendcmd_text(fr, np.minimum(mx, mw - (mww if mww is not None else mcw)),
                                        np.minimum(my, mh - (mhh if mhh is not None else mch)), "crop@fm", w=mww, h=mhh)
        self.n_calls += 1
        u = self.n_calls
        rel = self.rel
        OW, OH = g["OW"], g["OH"]
        al = (f"movie=filename={rel(self.st['_matte'])}:sp={times[0]:.6f},setpts=PTS-STARTPTS,"
              f"crop@fm=w={mcw}:h={mch}:x={min(mx[0], mw - mcw)}:y={min(my[0], mh - mch)},"
              f"scale={OW}:{OH}:flags=bicubic,format=gray[al{u}]")
        mode = self.bg["mode"]
        if mode == "blur":
            bgc = f"[f{u}b]scale={max(16, OW // 10 // 2 * 2)}:-2,boxblur=8:2,scale={OW}:{OH},eq=brightness=-0.04[bg{u}]"
            head = f"{pre}{crop},{scale},{lk},split=2[f{u}a][f{u}b]"
        else:
            head = f"{pre}{crop},{scale},{lk}[f{u}a]"
            col = str(self.bg.get("colour") or "#20303c").lstrip("#")
            base = f"color=c=0x{col}:s={OW}x{OH}:r={fr}"
            if mode == "image" and self.st.get("_bgimg"):
                bgc = (f"{base}[bb{u}];movie=filename={rel(self.st['_bgimg'])},scale={OW}:{OH}:"
                       f"force_original_aspect_ratio=increase,crop={OW}:{OH},setsar=1,format=yuv420p[bi{u}];"
                       f"[bb{u}][bi{u}]overlay=eof_action=repeat,format=yuv420p[bg{u}]")
            else:
                bgc = f"{base},format=yuv420p[bg{u}]"
        return (f"{head};{al};{bgc};[f{u}a][al{u}]alphamerge[fa{u}];"
                f"[bg{u}][fa{u}]overlay=shortest=1:format=auto,setsar=1,format=yuv420p"), cmd

    def rel(self, f: str) -> str:
        """A file named inside a filter: relative to the folder ffmpeg runs in (no drive letters or colons)."""
        return os.path.relpath(f, self.cwd or ".").replace("\\", "/")

    def kb_for(self, a: float, b: float) -> float:
        """Slow zoom for this part (a framing section with 'kb': how much it zooms in over the part)."""
        best = None
        for o in self.st.get("framing_overrides") or []:
            ov = min(b, float(o["end"])) - max(a, float(o["start"]))
            if o.get("kb") and ov > 0 and (best is None or ov > best[0]):
                best = (ov, float(o["kb"]))
        return min(0.3, max(0.0, best[1])) if best else 0.0

    def rect_for(self, a: float, b: float) -> dict | None:
        """The owner's own framing box: for this part (from a section override) or for the whole video."""
        if self.g["mode"] != "crop":
            return None
        best = None
        for o in self.st.get("framing_overrides") or []:
            r = o.get("rect")
            ov = min(b, float(o["end"])) - max(a, float(o["start"]))
            if r and ov > 0 and (best is None or ov > best[0]):
                best = (ov, r)
        return best[1] if best else (self.st.get("rect") or None)

    def fixed(self, rect: dict, n: int):
        """Box given as fractions of the picture (x, y = top-left, w = width); its height follows the output shape."""
        g = self.g
        W, H, asp = g["W"], g["H"], g["aspect"]
        cw = max(32.0, float(rect.get("w", 1)) * W)
        ch = cw / asp
        if ch > H:
            ch, cw = H, H * asp
        if cw > W:
            cw, ch = W, W / asp
        cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
        x = int(min(max(0, float(rect.get("x", 0)) * W), W - cw))
        y = int(min(max(0, float(rect.get("y", 0)) * H), H - ch))
        return cw, ch, np.full(n, x), np.full(n, y)


def encode_piece(ctx: Ctx, p: dict, args_in: list, vf: str, n: int, fr: Fraction, venc: list, gop: str, out: Path,
                 cwd: Path, complex_graph: bool = False):
    tmp = ff.tmp_of(out)
    fl = ["-filter_complex", vf, "-map", "[vout]"] if complex_graph else ["-map", "0:v:0", "-vf", vf]
    media.run_video(ctx, p, args_in + fl + ["-an", "-fps_mode", "cfr", "-r", str(fr), "-frames:v", n] + venc +
                    ["-g", gop] + COLOR + [tmp.name], duration=None, cwd=cwd)
    ff.finish(tmp, out)


def best_still(faces_d) -> float:
    s = (faces_d or {}).get("samples") or []
    good = [x for x in s if x.get("face")]
    if not good:
        return float(s[len(s) // 2]["t"]) if s else 1.0
    return float(max(good, key=lambda x: x["sharp"] * x["face"][4])["t"])


def title_ass(title: str, sub: str, W: int, H: int) -> str:
    def clean(t):
        return str(t).replace("{", "").replace("}", "").replace("\\", "").replace("\n", " ")
    size = int(H * 0.085)
    ev = []
    if title:
        ev.append(f"Dialogue: 0,0:00:00.00,9:59:59.00,Title,,0,0,0,,{{\\fad(400,400)}}{clean(title)}")
    if sub:
        ev.append(f"Dialogue: 0,0:00:00.00,9:59:59.00,Sub,,0,0,0,,{{\\fad(400,400)}}{clean(sub)}")
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Title,Georgia,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,2,2,5,{W // 10},{W // 10},0,1\n"
            f"Style: Sub,Georgia,{int(size * 0.5)},&H00E0E6EB,&H00FFFFFF,&H00000000,&H64000000,0,1,0,0,100,100,0,0,1,1,1,2,{W // 10},{W // 10},{int(H * 0.3)},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(ev) + "\n")


def cover_from_file(ctx, p, f: Path, n: int, fr: Fraction, g: dict, out: Path, venc, gop, d: Path) -> dict:
    """Your own photo (slow zoom, 'Ken Burns') or video (filled to the frame) as the cover picture."""
    if not f.is_file():
        raise JobError(f"Can't find the cover picture: {f}")
    info = ff.probe(f)
    vs = media.stream(info, "video")
    if not vs:
        raise JobError(f"{f.name} is not a picture or video.")
    OW, OH = g["OW"], g["OH"]
    fill = f"scale={OW}:{OH}:force_original_aspect_ratio=increase:flags=lanczos,crop={OW}:{OH}"
    still = vs.get("codec_name") in ("mjpeg", "png", "webp", "bmp", "tiff") or media.duration(info) < 0.5
    tm = media.tonemap_chain(vs)
    if still:
        big = f"scale={OW * 2}:{OH * 2}:force_original_aspect_ratio=increase:flags=lanczos,crop={OW * 2}:{OH * 2}"
        zoom = (f"zoompan=z='1+0.08*on/{int(n)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                f":s={OW}x{OH}:fps={fr}")
        vf = ",".join(x for x in (tm, big, zoom, "format=yuv420p") if x)
        encode_piece(ctx, p, ["-loop", "1", "-i", str(f)], vf, n, fr, venc, gop, out, d)
        return {"mode": "file", "kind": "photo"}
    vf = ",".join(x for x in (fill, tm, f"fps={fr}", "format=yuv420p") if x)
    encode_piece(ctx, p, ["-stream_loop", "-1", "-i", str(f)], vf, n, fr, venc, gop, out, d)
    return {"mode": "file", "kind": "video"}


def make_cover_clip(ctx, p, video, pics: Pictures, cover: dict, n: int, out: Path, analysis, faces_d, avoid,
                    venc, gop, d: Path):
    """The picture used for a covered range: other silent moments of the talk (dissolving into each other),
    a chosen moment, or a title card on a blurred still. Exactly n frames, already in the final look."""
    fr, g = pics.fr, pics.g
    mode = cover.get("mode") or "auto"
    if mode == "file" and cover.get("file"):
        return cover_from_file(ctx, p, Path(cover["file"]), n, fr, g, out, venc, gop, d)
    if mode == "footage" and cover.get("from") is not None:
        src = int(round(float(cover["from"]) * fr))
        vf, cmd = pics.vf(src, n, -1, out.stem + ".cmd")
        if cmd:
            (d / (out.stem + ".cmd")).write_text(cmd, encoding="utf-8")
        encode_piece(ctx, p, ["-ss", f"{float(Fraction(src) / fr):.6f}", "-i", str(video)], vf, n, fr, venc, gop, out, d)
        return {"mode": "footage"}
    clips = picture.listening_clips(analysis, faces_d, avoid) if mode == "auto" else []
    if mode == "auto" and clips:
        D = picture.DISSOLVE_S
        need = float(Fraction(n) / fr)
        chosen, tot, k = [], 0.0, 0
        while tot - D * max(0, len(chosen) - 1) < need + 0.2 and k < 300:
            c = clips[k % len(clips)]
            chosen.append(c)
            tot += c[1] - c[0]
            k += 1
        args, parts = [], []
        for i, (a, b) in enumerate(chosen):
            args += ["-ss", f"{a:.3f}", "-t", f"{b - a + 0.1:.3f}", "-i", str(video)]
            sv, _ = pics.vf(int(round(a * fr)), 1, -1, None)  # calm, still framing for each borrowed moment
            parts.append(f"[{i}:v]{sv},fps={fr},trim=duration={b - a:.3f},setpts=PTS-STARTPTS[c{i}]")
        graph = ";".join(parts)
        last, off = "c0", chosen[0][1] - chosen[0][0]
        for i in range(1, len(chosen)):
            graph += f";[{last}][c{i}]xfade=transition=fade:duration={D}:offset={off - D:.3f}[x{i}]"
            last = f"x{i}"
            off += (chosen[i][1] - chosen[i][0]) - D
        graph += f";[{last}]null[vout]"
        encode_piece(ctx, p, args, graph, n, fr, venc, gop, out, d, complex_graph=True)
        return {"mode": "auto", "moments": chosen}
    # title card (also used when no silent moment can be borrowed)
    t_still = float(cover["from"]) if cover.get("from") is not None else best_still(faces_d)
    still = d / (out.stem + "_still.png")
    sv, _ = pics.vf(int(round(t_still * fr)), 1, -1, None)
    media.run_video(ctx, p, ["-ss", f"{t_still:.3f}", "-i", str(video), "-vf", sv, "-frames:v", "1", still.name],
                    cwd=d)
    ass = d / (out.stem + ".ass")
    ass.write_text(title_ass(cover.get("title") or "", cover.get("subtitle") or "", g["OW"], g["OH"]),
                   encoding="utf-8")
    zoom = (f"zoompan=z='1+0.06*on/{int(n)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
            f":s={g['OW']}x{g['OH']}:fps={fr}")
    vf = f"boxblur=luma_radius=18:luma_power=2,eq=brightness=-0.12,{zoom},subtitles={ass.name},format=yuv420p"
    encode_piece(ctx, p, ["-loop", "1", "-i", still.name], vf, n, fr, venc, gop, out, d)
    return {"mode": "title"}


def render_pieces(ctx: Ctx, p: dict, video: Path, v: dict, fr: Fraction, segs, d: Path, kind: str, enc: str,
                  st: dict, reel_h: int, label: str, faces_d, analysis, covers: list) -> Path:
    """Plan the pieces (framing, punch-ins, covers with dissolves) and encode each one (resumable)."""
    d.mkdir(parents=True, exist_ok=True)
    pics = Pictures(v, kind, st, reel_h, faces_d, segs, fr)
    pics.cwd = d
    venc = vcodec(enc, kind, pics.g["OH"])
    gop = str(int(round(float(fr) * 2)))
    total = sum(b - a for a, b in segs)
    covers = list(covers) + list(p.get("_fill") or [])  # + the stretches with no camera picture
    cov_idx = picture.output_ranges_idx(segs, covers, fr) if covers else []
    cov_out = [(a, b) for a, b, _ in cov_idx]
    hard = frozenset(ci for ci, (_, _, idx) in enumerate(cov_idx) if covers[idx].get("hard"))
    pieces = picture.plan(segs, cov_out, fr, hard, xcuts=st.get("cut_style") == "dissolve" and kind == "full",
                          src_frames=p.get("_src_frames"))
    titles = p.get("titles_ass") if kind == "full" else None
    base_key = {"v": file_sig(video), "fr": str(fr), "g": pics.g, "enc": venc, "ver": 2,
                "st": {k: st.get(k) for k in ("layout", "res", "punch", "reelPunch", "colour", "framing_overrides", "rect",
                                         "background")},
                "bgfiles": [file_sig(Path(st[k])) for k in ("_matte", "_bgimg") if st.get(k)]}
    clip_files = {}
    cover_info = []
    avoid = [[float(c["start"]), float(c["end"])] for c in covers if not c.get("hard")]
    for ci in range(len(cov_out)):
        n = picture.cover_clip_frames(cov_out, ci, fr, total, hard)
        cover = covers[cov_idx[ci][2]]
        f = d / f"cover_{ci + 1}.mp4"
        key = key_of({**base_key, "cover": cover, "n": n, "faces": bool(faces_d), "ana": bool(analysis)})
        if not step_done(f, key):
            ctx.progress(None, f"{label}: making the cover picture {ci + 1}…")
            info = make_cover_clip(ctx, p, video, pics, cover, n, f, analysis, faces_d, avoid, venc, gop, d)
            mark_done(f, key, {"info": info})
        clip_files[ci] = f
        cover_info.append({**(read_done(f).get("info") or {}), "seconds": round(float(Fraction(n) / fr), 2)})
    names, done = [], 0
    for i, pc in enumerate(pieces):
        part = d / f"part_{i + 1:04d}.mp4"
        names.append(part.name)
        cmd_name = f"part_{i + 1:04d}.cmd"
        start = f"{float(Fraction(pc.src) / fr):.6f}"
        cg = False
        if pc.kind == "main":
            vf, cmd = pics.vf(pc.src, pc.n, pc.part, cmd_name)
            args = ["-ss", start, "-i", str(video)]
        elif pc.kind == "cover":
            vf, cmd = "null", None
            args = ["-ss", f"{float(Fraction(pc.coff) / fr):.6f}", "-i", clip_files[pc.cov].name]
        elif pc.kind == "xcut":  # smooth dissolve across a cut: the end of one part mixes into the next
            va, _ = pics.vf(pc.src, pc.n, pc.part, None)
            vb, _ = pics.vf(pc.extra["src2"], pc.n, pc.extra["part2"], None)
            o, L = pc.extra["tpos"], pc.extra["tlen"]
            k = f"min(1\\,max(0\\,(T*{float(fr):.4f}+{o}+0.5)/{L}))"
            vf = (f"[0:v]{va},setpts=PTS-STARTPTS[m0];[1:v]{vb},setpts=PTS-STARTPTS[m1];"
                  f"[m0][m1]blend=all_expr='A*(1-{k})+B*{k}',format=yuv420p[vout]")
            cmd = None
            args = ["-ss", start, "-i", str(video), "-ss", f"{float(Fraction(pc.extra['src2']) / fr):.6f}", "-i", str(video)]
            cg = True
        else:  # dissolve between the main picture and the cover picture
            mv, cmd = pics.vf(pc.src, pc.n, pc.part, cmd_name)
            o, L = pc.extra["tpos"], pc.extra["tlen"]
            k = f"min(1\\,max(0\\,(T*{float(fr):.4f}+{o})/{L}))"
            first, second = ("[m]", "[c]") if pc.kind == "in" else ("[c]", "[m]")
            vf = (f"[0:v]{mv},setpts=PTS-STARTPTS[m];[1:v]setpts=PTS-STARTPTS,format=yuv420p[c];"
                  f"{first}{second}blend=all_expr='A*(1-{k})+B*{k}',format=yuv420p[vout]")
            args = ["-ss", start, "-i", str(video), "-ss", f"{float(Fraction(pc.coff) / fr):.6f}", "-i",
                    clip_files[pc.cov].name]
            cg = True
        if titles:  # titles / lower thirds drawn onto the parts they cover
            vf = pro.add_to_filter(vf, pro.titles_filter(d, titles, float(Fraction(done) / fr),
                                                         float(Fraction(done + pc.n) / fr)), cg)
        key = key_of({**base_key, "pc": [pc.kind, pc.n, pc.part, pc.src, pc.cov, pc.coff, pc.extra], "vf": vf,
                      "cmd": key_of(cmd) if cmd else None,
                      "clip": read_done(clip_files[pc.cov]).get("key") if pc.cov >= 0 else None})
        if not step_done(part, key):
            if cmd:
                (d / cmd_name).write_text(cmd, encoding="utf-8")
            ctx.progress(done / total, f"{label}: part {i + 1} of {len(pieces)}…")
            encode_piece(ctx, p, args, vf, pc.n, fr, venc, gop, part, d, complex_graph=cg)
            mark_done(part, key)
        done += pc.n
        ctx.progress(done / total, f"{label}: part {i + 1} of {len(pieces)} done")
    lst = d / "list.txt"
    lst.write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    p.setdefault("_cover_info", {})[kind] = cover_info
    return lst


def assemble_audio(clean: Path, segs, fr: Fraction, out: Path, xfade_ms: float = 40.0, pre: float = 0.0,
                   gains: list | None = None):
    """Cut the cleaned sound exactly like the picture. At every cut the two sides cross-fade (equal power,
    centred on the cut, using the sound just past each edge), so joins are smooth and click-free while the
    total length - and lip sync - stay exact."""
    arr, rate, ch = open_pcm16(clean)
    total_src = arr.shape[0]
    tmp = ff.tmp_of(out)
    w = Writer(tmp, rate, ch)

    def src(a: int, b: int) -> np.ndarray:  # source samples a..b, silence outside the file
        x = np.zeros((b - a, ch), np.float32)
        lo, hi = max(0, a), min(total_src, b)
        if hi > lo:
            x[lo - a:hi - a] = np.asarray(arr[lo:hi], dtype=np.float32) / 32768.0
        return x

    # sample positions of every part: start in the source, length in the output (exact, frame based)
    parts, cum = [], 0
    for a, b in segs:
        o0 = int(round(Fraction(cum) / fr * rate))
        cum += b - a
        parts.append((int(round((Fraction(a) / fr + Fraction(pre).limit_denominator(1000000)) * rate)),
                      int(round(Fraction(cum) / fr * rate)) - o0))  # (the cleaned sound starts at video time -pre)
    want = int(round(float(xfade_ms) / 2000 * rate)) if xfade_ms and xfade_ms > 0 else 0
    halves = [min(want, parts[k][1] // 3, parts[k + 1][1] // 3) for k in range(len(parts) - 1)]
    for k, (s0, n) in enumerate(parts):
        left = halves[k - 1] if k > 0 else 0
        right = halves[k] if k < len(parts) - 1 else 0
        body = src(s0 + left, s0 + n - right)
        g_k = 10 ** (gains[k] / 20) if gains and gains[k] else 1.0  # the voice level set for this clip
        if g_k != 1.0:
            body *= g_k
        if want == 0 and len(parts) > 1 and n > 2 * int(FADE_S * rate):  # no cross-fade: tiny fades, no clicks
            f = int(FADE_S * rate)
            ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)[:, None]
            if k > 0:
                body[:f] *= ramp
            if k < len(parts) - 1:
                body[-f:] *= ramp[::-1]
        w.write(body)
        if right:
            h = right
            ns0 = parts[k + 1][0]
            ang = np.linspace(0.0, np.pi / 2, 2 * h, dtype=np.float32)[:, None]
            g_n = 10 ** (gains[k + 1] / 20) if gains and gains[k + 1] else 1.0
            w.write(src(s0 + n - h, s0 + n + h) * (np.cos(ang) * g_k) + src(ns0 - h, ns0 + h) * (np.sin(ang) * g_n))
    w.close()
    ff.finish(tmp, out)


def concat_mux(ctx: Ctx, lst: Path, wav: Path, out: Path, dur: float, msg: str, burn_ass: Path | None = None,
               enc: str = "libx264", out_h: int = 1920):
    tmp = ff.tmp_of(out).resolve()
    args = ["-f", "concat", "-safe", "0", "-i", lst.resolve(), "-i", wav.resolve(), "-map", "0:v:0", "-map", "1:a:0"]
    if burn_ass:
        args += ["-vf", f"subtitles={burn_ass.name}," + ("format=nv12" if enc == "h264_qsv" else "format=yuv420p")]
        args += vcodec(enc, "reel", out_h) + COLOR
    else:
        args += ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", tmp]
    # libass wants a plain file name, so ffmpeg runs inside the reel folder (inputs use full paths)
    ff.run_ff(ctx, args, duration=dur, msg=msg, cwd=burn_ass.parent if burn_ass else None)
    ff.finish(tmp, out)


def _pcm16k(path, start: float, dur: float) -> np.ndarray:
    cmd = [ff.tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, start):.4f}", "-i", str(path), "-t", f"{dur:.4f}",
           "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000 if config.IS_WIN else 0)
    return np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768.0


def lipsync_qc(final: Path, cam16: Path, segs, fr: Fraction, src_frames: int | None = None) -> list[dict]:
    """At 10% / 50% / 90% of the finished video, compare its sound with the camera's own sound at the same
    picture frame. A perfect render shows 0 ms (plus whatever sync error the recording itself had)."""
    starts, cum = [], 0
    for a, b in segs:
        starts.append(cum)
        cum += b - a
    total = cum / fr
    W, S = 8.0, 0.5
    out = []
    for f in (0.1, 0.5, 0.9):
        want = float(total) * f
        best = None
        for (a, b), o in zip(segs, starts):
            o_s, len_s = float(Fraction(o) / fr), float(Fraction(b - a) / fr)
            if len_s < W + 1:
                continue
            if src_frames is not None:  # only parts that have the camera picture (and its sound)
                cs, ce = max(a, 0), min(b, src_frames)
                if (ce - cs) / fr < W + 2 * S + 1:
                    continue
                o_s, len_s = o_s + float(Fraction(cs - a) / fr), float(Fraction(ce - cs) / fr)
                a = cs
            t = min(max(want, o_s + 0.5), o_s + len_s - W - 0.5)
            dist = abs(t - want)
            if best is None or dist < best[0]:
                best = (dist, t, float(Fraction(a) / fr) + (t - o_s))
        if not best:
            out.append({"at": round(want, 1), "ok": None, "note": "no part long enough to measure"})
            continue
        _, t_out, t_src = best
        y = _pcm16k(final, t_out, W)
        x, rate, s0 = syncer.read_window(cam16, t_src - S, W + 2 * S)
        if len(y) < rate * 2 or len(x) < len(y):
            out.append({"at": round(t_out, 1), "ok": None, "note": "could not read the sound"})
            continue
        lag, conf = syncer.xcorr(y, x, 0, len(x) - len(y), phat=0.8)
        # camera sound at t_src lines up with rendered sound at t_out; offset > 0 means sound is late
        off_ms = ((t_src - s0) - lag / rate) * 1000
        good = conf >= 4
        out.append({"at": round(t_out, 1), "offset_ms": round(off_ms, 1), "confidence": round(conf, 1),
                    "ok": (abs(off_ms) < 20) if good else None,
                    "note": "" if good else "too little matching sound here to measure reliably"})
    return out


def measure_loudness(ctx: Ctx, path: Path, dur: float) -> dict:
    text = ff.run_ff(ctx, ["-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
                     duration=dur, msg="Checking loudness…", loglevel="info", want_stderr=True)
    import re
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return {"lufs": float(i[-1]) if i else None, "true_peak": float(tp[-1]) if tp else None}


def picture_problems(ctx: Ctx, path: Path, dur: float) -> dict:
    """Black frames and frozen picture (e.g. the camera image got stuck) in the finished video."""
    text = ff.run_ff(ctx, ["-i", path, "-map", "0:v:0", "-vf",
                           "fps=10,scale=320:-2,blackdetect=d=0.5:pix_th=0.08,freezedetect=n=-60dB:d=4",
                           "-an", "-f", "null", "-"], duration=dur, msg="Checking for black or frozen picture…",
                     loglevel="info", want_stderr=True)
    black = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", text)]
    fs = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", text)]
    fe = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", text)]
    frozen = [(a, fe[i] if i < len(fe) else dur) for i, a in enumerate(fs)]
    return {"black": black, "frozen": frozen}


def fmt(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def fill_covers(st: dict, pre: float, post: float, vdur: float) -> list[dict]:
    """Covers for the stretches where only the recorder was running: the picture is filled (listening shots of
    the speaker, a title card, or the owner's photo); the sound carries on. Hard cuts at their edges."""
    mode = st.get("fill") or "auto"
    base = {"mode": mode if mode in ("auto", "title", "file") else "auto", "hard": True,
            "title": st.get("fill_title") or "", "file": st.get("fill_file") or None, "from": None}
    out = []
    if pre > 0:
        out.append({**base, "start": -pre - 1.0, "end": 0.0})  # (a little extra: rounding to frames)
    if post > 0:
        out.append({**base, "start": vdur, "end": vdur + post + 1.0})
    return out


def job_render(ctx: Ctx, p: dict) -> dict:
    video = media._need(p.get("video"), "video")
    vinfo = ff.probe(video)
    v = media.stream(vinfo, "video")
    if not v:
        raise JobError("That file has no picture.")
    vdur = media.duration(vinfo)
    fr = Fraction(media.output_fps(v))
    total_frames = int(vdur * fr)
    st = p.get("settings") or {}
    _background_files(video, st)
    st["_lut"] = pro.lut_file(video, st.get("colour"))
    p["_src_frames"] = total_frames
    # 'keep the whole recording': the timeline also covers the recorder before / after the camera
    pre, post = audio.span(p, vdur)
    pre_f, post_f = int(round(pre * fr)), int(round(post * fr))
    p["_fill"] = fill_covers(st, pre, post, float(Fraction(total_frames) / fr))
    keep = p.get("keep") or [[-pre, vdur + post]]
    segs = frames_of(keep, fr, total_frames + post_f, lo=-pre_f)
    if not segs:
        raise JobError("Nothing left to render — everything is cut.")
    hw = hardware.report()
    enc = st.get("enc") or "auto"
    enc = hw["best_encoder"] if enc == "auto" else enc
    res = str(st.get("res") or "1080")
    reel_h = 1280 if str(st.get("reelRes")) == "720" else 1920
    out_dir = video.parent
    wd = media.work_dir(str(video)) / "render"
    wd.mkdir(exist_ok=True)
    edited_s = float(Fraction(sum(b - a for a, b in segs)) / fr)
    reels = p.get("reels") or []
    reel_s = sum(float(r.get("end", 0)) - float(r.get("start", 0)) for r in reels)
    gb_per_h = {"orig": 25, "1440": 10, "1080": 5}.get(res, 5)
    hardware.disk_check(out_dir, 1 + (edited_s * 2 * gb_per_h + reel_s * 12) / 3600 + edited_s / 3600 * 1.5,
                        "the finished video")
    w_full = 0.8 if reels else 0.95
    faces_d = faces.load(str(video))
    try:
        analysis = json.loads((media.work_dir(str(video)) / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        analysis = None
    covers = [c for c in (p.get("covers") or []) if float(c.get("end", 0)) > float(c.get("start", 0))]
    if st.get("layout") in ("crop169", "blur169") and not faces_d:
        ctx.log("No face analysis yet: framing stays centred (run 'Find the speaker' for automatic framing).")
    # 1. sound
    ctx.stage(0.0, 0.12)
    cl = audio.ensure_clean(ctx, p)
    clean = Path(cl["clean_audio"])
    # 2. picture parts
    ctx.stage(0.12, 0.12 + w_full * 0.8)
    lst = render_pieces(ctx, p, video, v, fr, segs, wd / "full", "full", enc, st, reel_h, "Full video",
                        faces_d, analysis, covers)
    ctx.stage(0.12 + w_full * 0.8, 0.12 + w_full * 0.85)
    ctx.progress(0, "Putting the sound together…")
    full_wav = wd / "edited_audio.wav"
    xf = float(st.get("xfade_ms", 40) or 0)
    assemble_audio(clean, segs, fr, full_wav, xf, pre=pre, gains=pro.voice_gains(p, segs, fr))
    music = p.get("music") or {}
    if music.get("file"):
        ctx.progress(0.3, "Mixing the background music…")
        mixed = wd / "edited_audio_music.wav"
        pro.mix_music(ctx, full_wav, music, mixed, edited_s)
        full_wav = mixed
    if music.get("file") or any(g.get("db") for g in (p.get("voice_gains") or [])):
        leveled = wd / "edited_audio_level.wav"
        pro.relevel(ctx, full_wav, audio.settings(p)["lufs"], edited_s, leveled)
        full_wav = leveled
    final = out_dir / "Edited - full video.mp4"
    concat_mux(ctx, lst, full_wav, final, edited_s, "Joining the full video…")
    ctx.progress(0.6, "Making the podcast files…")
    shutil.copyfile(full_wav, out_dir / "Edited - full audio.wav")
    ff.run_ff(ctx, ["-i", full_wav, "-c:a", "libmp3lame", "-b:a", "192k", out_dir / "Edited - full audio.mp3"],
              duration=edited_s, msg="Making the podcast MP3…")
    if p.get("srt"):
        (out_dir / "Edited - full video.srt").write_text(p["srt"], encoding="utf-8")
    if p.get("chapters"):
        (out_dir / "Edited - chapters.txt").write_text(p["chapters"] + "\n", encoding="utf-8")
    if p.get("srt"):
        (out_dir / "Edited - full video.vtt").write_text(exports.vtt_from_srt(p["srt"]), encoding="utf-8")
    if p.get("youtube"):
        (out_dir / "Edited - YouTube.txt").write_text(p["youtube"] + "\n", encoding="utf-8")
    # a timeline for DaVinci Resolve (free) / Final Cut, and a plain cut list, so a person can fine-tune by hand
    g_full = geometry(v, "full", st, reel_h)
    W0, H0 = g_full["W"], g_full["H"]
    chap = exports.parse_chapters(p.get("chapters") or "")
    (out_dir / "Edited - timeline for DaVinci Resolve.fcpxml").write_text(
        exports.fcpxml(video, clean, segs, fr, W0, H0, total_frames, g_full["OW"], g_full["OH"],
                       name=video.stem + " (edited)", chapters=chap, pre_f=pre_f, post_f=post_f), encoding="utf-8")
    (out_dir / "Edited - cut list.edl").write_text(exports.edl(video, segs, fr, src_frames=total_frames), encoding="utf-8")
    proj = {k: p.get(k) for k in ("video", "audio", "sync", "sound", "keep", "covers", "settings", "chapters")}
    (out_dir / "Edited - project.json").write_text(json.dumps(proj, indent=1), encoding="utf-8")
    outputs = [str(final), str(out_dir / "Edited - full audio.mp3"), str(out_dir / "Edited - full audio.wav")]
    # 3. reels
    lo = 0.12 + w_full * 0.9
    for ri, r in enumerate(reels):
        ctx.stage(lo + (0.95 - lo) * ri / len(reels), lo + (0.95 - lo) * (ri + 1) / len(reels))
        rsegs = frames_of(r.get("segs") or [[r["start"], r["end"]]], fr, total_frames + post_f, lo=-pre_f)
        if not rsegs:
            continue
        rd = wd / f"reel_{ri + 1}"
        rl = render_pieces(ctx, p, video, v, fr, rsegs, rd, "reel", enc, st, reel_h, f"Reel {ri + 1}",
                           faces_d, None, [])
        rwav = rd / "audio.wav"
        assemble_audio(clean, rsegs, fr, rwav, xf, pre=pre, gains=pro.voice_gains(p, rsegs, fr))
        if music.get("file") and music.get("reels"):
            rmix = rd / "audio_music.wav"
            rdur = float(Fraction(sum(b - a for a, b in rsegs)) / fr)
            pro.mix_music(ctx, rwav, music, rmix, rdur)
            rwav = rd / "audio_level.wav"
            pro.relevel(ctx, rmix, audio.settings(p)["lufs"], rdur, rwav)
        name = "".join(c for c in str(r.get("name") or f"Reel {ri + 1:02d}") if c not in '\\/:*?"<>|').strip()[:80]
        rout = out_dir / f"{name}.mp4"
        ass = None
        if r.get("ass") and st.get("reelCaps", True):
            ass = rd / "captions.ass"
            ass.write_text(r["ass"], encoding="utf-8")
        concat_mux(ctx, rl, rwav, rout, float(Fraction(sum(b - a for a, b in rsegs)) / fr),
                   f"Reel {ri + 1}: finishing…", burn_ass=ass, enc=enc, out_h=reel_h)
        outputs.append(str(rout))
    # extra copies (export presets): small for WhatsApp, square for the Instagram / Facebook feed
    for x in pro.extra_copies(ctx, final, st.get("extras") or [], edited_s):
        outputs.append(str(x))
    # 4. quality check
    ctx.stage(0.95, 1.0)
    ctx.progress(0, "Quality check: lip sync at 3 points…")
    qc = {"lipsync": [], "expected_s": round(edited_s, 3)}
    cam16 = media.work_dir(str(video)) / "video_16k.wav"
    if cam16.exists():
        qc["lipsync"] = lipsync_qc(final, cam16, segs, fr, src_frames=total_frames)
    got = media.duration(ff.probe(final))
    qc["duration_s"] = round(got, 3)
    qc["duration_ok"] = abs(got - edited_s) < 0.1
    qc["loudness"] = measure_loudness(ctx, final, edited_s)
    ctx.progress(0.4, "Quality check: picture…")
    qc["picture"] = picture_problems(ctx, final, edited_s)
    if p.get("srt"):
        qc["captions"] = exports.caption_check(p["srt"], edited_s)
    s = audio.settings(p)
    lu = qc["loudness"].get("lufs")
    qc["loudness_ok"] = lu is not None and abs(lu - s["lufs"]) <= 1.0 and (qc["loudness"].get("true_peak") or 0) <= -1.0
    lines = [f"Quality check — {final.name}", ""]
    for l in qc["lipsync"]:
        if l.get("ok") is None:
            lines.append(f"?  Lip sync at {fmt(l['at'])}: not measured ({l.get('note')})")
        else:
            lines.append(f"{'✓' if l['ok'] else '⚠'}  Lip sync at {fmt(l['at'])}: {l['offset_ms']:+.1f} ms "
                         f"({'sound late' if l['offset_ms'] > 0 else 'sound early'}; must be under 20 ms)")
    if not qc["lipsync"]:
        lines.append("?  Lip sync: not measured (run Prepare first so the camera sound is available)")
    lines.append(f"{'✓' if qc['loudness_ok'] else '⚠'}  Loudness {lu if lu is not None else '?'} LUFS "
                 f"(target {s['lufs']}), true peak {qc['loudness'].get('true_peak')} dB (limit −1.5)")
    lines.append(f"{'✓' if qc['duration_ok'] else '⚠'}  Length {fmt(got)} (expected {fmt(edited_s)})")
    pic = qc["picture"]
    if not pic["black"] and not pic["frozen"]:
        lines.append("✓  Picture: no black or frozen stretches")
    for a, b in pic["black"]:
        lines.append(f"⚠  Black picture {fmt(a)} – {fmt(b)}")
    for a, b in pic["frozen"]:
        lines.append(f"⚠  Picture does not change {fmt(a)} – {fmt(b)} (frozen camera image? fine if intended)")
    if "captions" in qc:
        iss = qc["captions"]["issues"]
        lines.append(f"{'✓' if not iss else '⚠'}  Captions: {qc['captions']['cues']} captions"
                     + (", all easy to read" if not iss else f", {len(iss)} to look at (first at {fmt(iss[0]['t'])}: {iss[0]['text']})"))
    lines.append(f"   Video encoder: {hardware.report()['best_encoder_name'] if (st.get('enc') or 'auto') == 'auto' else enc}")
    qc["text"] = "\n".join(lines)
    (out_dir / "Edited - quality check.txt").write_text(qc["text"] + "\n", encoding="utf-8")
    qc_ok = (all(l.get("ok") is not False for l in qc["lipsync"]) and qc["duration_ok"] and qc["loudness_ok"]
             and not qc["picture"]["black"] and not qc["picture"]["frozen"]
             and not (qc.get("captions") or {}).get("issues"))
    return {"outputs": outputs, "qc": qc, "folder": str(out_dir), "encoder": enc, "clean": cl,
            "covers": (p.get("_cover_info") or {}).get("full", []),
            "message": ("Finished ✓ — quality check passed." if qc_ok else "Finished — the quality check found something to look at (see below).")}


def _background_files(video, st: dict):
    """Background replacement needs the person cut-out (matte) and, for 'my picture', a copy of that picture
    inside studio_work (so the filter can name it without drive letters)."""
    bg = st.get("background") or {}
    if bg.get("mode") not in ("blur", "colour", "image"):
        return
    m = matte.find(str(video))
    if not m:
        raise JobError("To change the background, first press “Find the person” in the Picture panel "
                       "(it takes a while; it is done once per video).")
    st["_matte"] = str(m)
    if bg.get("mode") == "image":
        src = Path(str(bg.get("file") or ""))
        if not src.is_file():
            raise JobError("Choose the background picture first (Picture panel → Background → Choose picture).")
        dst = media.work_dir(str(video)) / ("background" + src.suffix.lower())
        if not dst.exists() or dst.read_bytes() != src.read_bytes():  # (copy only when it changed: keeps finished parts)
            shutil.copyfile(src, dst)
        st["_bgimg"] = str(dst)


def job_frame(ctx: Ctx, p: dict) -> dict:
    """One finished-look still (framing, punch-in, colour) at a moment of the source - to check before rendering."""
    video = media._need(p.get("video"), "video")
    vinfo = ff.probe(video)
    v = media.stream(vinfo, "video")
    if not v:
        raise JobError("That file has no picture.")
    fr = Fraction(media.output_fps(v))
    st = p.get("settings") or {}
    t = max(0.0, min(float(p.get("t", 0)), media.duration(vinfo) - 0.1))
    a = int(round(t * fr))
    zoom_part = bool(p.get("closeup"))
    w = int(5 * fr)  # parts around the moment (colour matching looks at a few seconds)
    segs = [(max(0, a - 3 * w), max(1, a - w)), (max(0, a - w), a + w)] if zoom_part else [(max(0, a - w), a + w)]
    _background_files(video, st)
    st["_lut"] = pro.lut_file(video, st.get("colour"))
    d = media.work_dir(str(video)) / "frames"
    d.mkdir(exist_ok=True)
    pics = Pictures(v, "full", st, 1920, faces.load(str(video)), segs, fr)
    pics.cwd = d
    vf, _ = pics.vf(a, 1, len(segs) - 1, None)
    for old in d.glob("*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    out = d / f"frame_{key_of([t, st, zoom_part, file_sig(video)])}.jpg"
    media.run_video(ctx, p, ["-ss", f"{t:.3f}", "-i", str(video), "-vf", vf, "-frames:v", "1", "-q:v", "3", out.name],
                    cwd=d)
    return {"frame": str(out), "size": [pics.g["OW"], pics.g["OH"]], "message": "Preview still ready."}
