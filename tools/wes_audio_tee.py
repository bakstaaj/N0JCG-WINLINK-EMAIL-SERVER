#!/usr/bin/env python3
"""Forward WES PCM to Dire Wolf while recording raw input metrics."""
from __future__ import annotations
import json
import math
import os
import struct
import sys
import time

RATE = 48000
WINDOW_SAMPLES = RATE // 5
METRICS = os.environ.get("N0JCG_AUDIO_METRICS", "/run/n0jcg-winlink/audio-meter.jsonl")

def emit(values: list[int]) -> None:
    if not values:
        return
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    peak = max(abs(v) for v in values)
    record = {
        "timestamp": time.time(),
        "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 1),
        "peak_dbfs": round(20 * math.log10(max(peak, 1) / 32768), 1),
        "sample_count": len(values),
    }
    try:
        os.makedirs(os.path.dirname(METRICS), exist_ok=True)
        with open(METRICS, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        # Keep the diagnostic file bounded while retaining recent history.
        if os.path.getsize(METRICS) > 1024 * 1024:
            with open(METRICS, "r+b") as stream:
                stream.seek(-512 * 1024, os.SEEK_END)
                tail = stream.read()
            with open(METRICS, "wb") as stream:
                stream.write(tail)
    except OSError:
        pass

def main() -> int:
    pending = b""
    values: list[int] = []
    while True:
        block = sys.stdin.buffer.read(4096)
        if not block:
            break
        sys.stdout.buffer.write(block)
        sys.stdout.buffer.flush()
        pending += block
        usable = len(pending) - (len(pending) % 2)
        if usable:
            values.extend(struct.unpack("<%dh" % (usable // 2), pending[:usable]))
            pending = pending[usable:]
        while len(values) >= WINDOW_SAMPLES:
            emit(values[:WINDOW_SAMPLES])
            del values[:WINDOW_SAMPLES]
    emit(values)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
