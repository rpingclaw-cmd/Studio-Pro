"""Stage 4: exports (WebVTT, FCPXML for DaVinci Resolve, EDL, project), final quality check (frozen / black
picture, caption readability) and thumbnail suggestions."""
import json
import xml.dom.minidom
from pathlib import Path

import numpy as np

from .conftest import probe, run
from .test_stage3 import FACE, detect, face_media, frames_at, pytestmark  # noqa: F401  (shared fixture + skip)


def test_exports_and_final_check(eng, face_media):
    keep = [[0, 8.0], [9.0, 20.0], [21.0, 30.0]]
    srt = ("1\n00:00:01,000 --> 00:00:03,000\nThe heart is the field of action\n\n"
           "2\n00:00:03,000 --> 00:00:03,300\nToo quick\n\n")  # second caption is too short on purpose
    r = run(eng, "render", {"video": face_media["video"], "keep": keep, "sound": {"strength": 0}, "srt": srt,
                            "chapters": "0:00 Opening\n0:12 The heart", "youtube": "Title\n\nDescription",
                            "settings": {"res": "1080", "layout": "crop169", "enc": "auto"}})
    out = Path(face_media["video"]).parent
    vtt = (out / "Edited - full video.vtt").read_text()
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.000" in vtt
    fx = (out / "Edited - timeline for DaVinci Resolve.fcpxml").read_text()
    doc = xml.dom.minidom.parseString(fx.split("<!DOCTYPE fcpxml>")[1])
    clips = [c for c in doc.getElementsByTagName("asset-clip") if c.getAttribute("ref") == "r3"]
    assert len(clips) == 3 and [m.getAttribute("value") for m in doc.getElementsByTagName("marker")] == ["Opening", "The heart"]
    assert "file://" in doc.getElementsByTagName("media-rep")[0].getAttribute("src")
    edl = (out / "Edited - cut list.edl").read_text()
    assert edl.count("AA/V  C") == 3
    proj = json.loads((out / "Edited - project.json").read_text())
    assert proj["keep"] == keep
    assert (out / "Edited - YouTube.txt").read_text().startswith("Title")
    qc = r["qc"]
    assert qc["captions"]["issues"] and "0.30 s" in qc["captions"]["issues"][0]["text"]
    assert qc["picture"]["frozen"], "a still photo on a still background must be reported as frozen picture"
    assert not qc["picture"]["black"]
    assert "Captions" in qc["text"] and "frozen" in qc["text"]


def test_thumbnail_suggestions(eng, face_media):
    run(eng, "faces", {"video": face_media["video"]})
    r = run(eng, "thumbs", {"video": face_media["video"], "n": 4})
    th = r["thumbs"]
    assert 3 <= len(th) <= 4
    ts = [t["t"] for t in th]
    assert min(np.diff(ts)) >= 3, ts  # spread over the talk
    for t in th:
        v = next(s for s in probe(t["file"])["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (1280, 720)
        import cv2
        img = cv2.imread(t["file"])
        f = detect(img)
        assert f and 0.3 < f[0] < 0.7 and f[2] > 0.3  # a centred, big face
