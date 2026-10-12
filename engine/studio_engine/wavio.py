"""Small helpers to read/write 16-bit PCM WAV files without loading hour-long files into memory."""
import struct
import wave
from pathlib import Path

import numpy as np


def open_pcm16(path) -> tuple[np.ndarray, int, int]:
    """Memory-map a 16-bit PCM WAV. Returns (int16 array shaped [frames, channels], rate, channels)."""
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path.name} is not a WAV file")
        rate = ch = bits = None
        pos = 12
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError(f"{path.name}: no audio data")
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            pos += 8
            if cid == b"fmt ":
                fmt = f.read(size)
                _, ch, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                f.seek(pos + size + (size & 1))
            elif cid == b"data":
                data_off = pos
                total = path.stat().st_size - data_off
                if size and size != 0xFFFFFFFF:  # ffmpeg writes 0xFFFFFFFF for streamed / >4 GB files
                    total = min(total, size)
                break
            else:
                f.seek(pos + size + (size & 1))
            pos += size + (size & 1)
    if bits != 16:
        raise ValueError(f"{path.name}: expected 16-bit audio")
    n = total // (2 * ch)
    arr = np.memmap(path, dtype="<i2", mode="r", offset=data_off, shape=(n, ch))
    return arr, rate, ch


def read_mono_f32(path, start_s: float = 0.0, dur_s: float | None = None) -> tuple[np.ndarray, int]:
    arr, rate, ch = open_pcm16(path)
    a = int(max(0, start_s) * rate)
    b = arr.shape[0] if dur_s is None else min(arr.shape[0], a + int(dur_s * rate))
    x = np.asarray(arr[a:b], dtype=np.float32) / 32768.0
    return (x.mean(axis=1) if ch > 1 else x[:, 0]), rate


class Writer:
    """Streamed 16-bit WAV writer."""

    def __init__(self, path, rate: int, ch: int):
        self.w = wave.open(str(path), "wb")
        self.w.setnchannels(ch)
        self.w.setsampwidth(2)
        self.w.setframerate(rate)
        self.ch = ch

    def write(self, x: np.ndarray):
        if x.ndim == 1 and self.ch > 1:
            x = np.repeat(x[:, None], self.ch, axis=1)
        self.w.writeframes(np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes())

    def write_i16(self, x: np.ndarray):
        self.w.writeframes(np.ascontiguousarray(x, dtype="<i2").tobytes())

    def close(self):
        self.w.close()
