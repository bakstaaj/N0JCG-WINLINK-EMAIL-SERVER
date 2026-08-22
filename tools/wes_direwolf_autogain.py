#!/usr/bin/env python3
"""Operator-run WES RMS receive calibration."""
from __future__ import annotations
import argparse
import json
import os
import re
import select
import statistics
import signal
import subprocess
import sys
import time

LEVEL_RE = re.compile(r"audio level = (\d+)")
PACKET_RE = re.compile(r"\]\s+[A-Z0-9-]+>[A-Z0-9,-]+:")
VALUE_RE = re.compile(r"(?:^|\s): values=(\d+)")
METER_FILE = os.environ.get("N0JCG_AUDIO_METRICS", "/run/n0jcg-winlink/audio-meter.jsonl")
GAIN_FILE = os.environ.get("N0JCG_GAIN_FILE", "/var/lib/n0jcg-winlink-webmail/digirig-gain")

def mixer_value() -> int:
    result = subprocess.run(["amixer", "-c", "Device", "cget", "numid=8"], check=True, capture_output=True, text=True)
    match = VALUE_RE.search(result.stdout)
    if not match:
        raise RuntimeError("Mic Capture Volume (numid=8) was not found")
    return int(match.group(1))

def set_mixer(value: int) -> None:
    subprocess.run(["amixer", "-c", "Device", "cset", "numid=8", str(value)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        os.makedirs(os.path.dirname(GAIN_FILE), exist_ok=True)
        with open(GAIN_FILE, "w", encoding="ascii") as stream:
            stream.write(f"{value}\n")
    except OSError as exc:
        print(f"Warning: could not persist DigiRig gain: {exc}", flush=True)

def raw_metrics(since: float, event_times: list[float]) -> tuple[float | None, float | None, float | None]:
    peak = None
    rms = None
    records = []
    try:
        with open(METER_FILE, encoding="utf-8") as stream:
            for line in stream.readlines()[-600:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(item.get("timestamp", 0)) < since:
                    continue
                records.append(item)
                if item.get("peak_dbfs") is not None:
                    value = float(item["peak_dbfs"])
                    peak = value if peak is None else max(peak, value)
                if item.get("rms_dbfs") is not None:
                    rms = float(item["rms_dbfs"])
    except OSError:
        pass
    snr = None
    if event_times and records:
        signal = [float(item["rms_dbfs"]) for item in records if any(abs(float(item.get("timestamp", 0)) - event) <= 0.75 for event in event_times) and item.get("rms_dbfs") is not None]
        noise = [float(item["rms_dbfs"]) for item in records if not any(abs(float(item.get("timestamp", 0)) - event) <= 0.75 for event in event_times) and item.get("rms_dbfs") is not None]
        # Use robust medians rather than the single highest signal sample. A
        # peak can land on a packet edge (or on a meter transient), producing
        # materially different S/N values on otherwise identical runs.
        if len(signal) >= 2 and len(noise) >= 2:
            snr = round(statistics.median(signal) - statistics.median(noise), 1)
    return peak, rms, snr

def run_attempt(target: str, callsign: str, timeout: int) -> tuple[list[int], float | None, float | None, float | None]:
    started = time.time()
    journal = subprocess.Popen(["journalctl", "-u", "n0jcg-direwolf.service", "-f", "-n", "0", "--no-pager", "-o", "cat"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    pat = subprocess.Popen(["pat", "--mycall", callsign, "connect", f"ax25+agwpe:///{target}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env={**os.environ, "PAT_MYCALL": callsign})
    levels: list[int] = []
    pending_levels: list[tuple[int, float]] = []
    decoded_levels: list[int] = []
    event_times: list[float] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if not journal.stdout:
                continue
            ready, _, _ = select.select([journal.stdout], [], [], min(0.25, max(0.0, deadline - time.monotonic())))
            if not ready:
                continue
            line = journal.stdout.readline()
            if not line:
                continue
            match = LEVEL_RE.search(line)
            if match:
                pending_levels.append((int(match.group(1)), time.time()))
                levels.append(int(match.group(1)))
                pending_levels = pending_levels[-8:]
            # Dire Wolf prints the audio-level line before the decoded AX.25
            # frame. Correlate only the nearest recent level with that frame;
            # an audio-level line without a decoded packet is noise/activity,
            # not valid signal for S/N.
            if PACKET_RE.search(line):
                decoded_at = time.time()
                candidates = [(level, stamp) for level, stamp in pending_levels if 0 <= decoded_at - stamp <= 2.0]
                if candidates:
                    level, stamp = candidates[-1]
                    decoded_levels.append(level)
                    event_times.append(stamp)
                    pending_levels = [(item_level, item_stamp) for item_level, item_stamp in pending_levels if item_stamp > stamp]
                if len(decoded_levels) >= 3:
                    break
        peak, rms, snr = raw_metrics(started, event_times)
        return decoded_levels, peak, rms, snr
    finally:
        os.killpg(pat.pid, signal.SIGTERM)
        try:
            pat.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(pat.pid, signal.SIGKILL)
        journal.terminate()
        try:
            journal.wait(timeout=2)
        except subprocess.TimeoutExpired:
            journal.kill()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=os.environ.get("N0JCG_RMS_TARGET", "N0JCG-10"))
    parser.add_argument("--callsign", default=os.environ.get("N0JCG_CALIBRATION_CALLSIGN", "N0JCG-3"))
    parser.add_argument("--low", type=int, default=50)
    parser.add_argument("--high", type=int, default=64)
    parser.add_argument("--stable", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--raw-low", type=float, default=-24.0)
    parser.add_argument("--raw-high", type=float, default=-6.0)
    parser.add_argument("--noise-high", type=float, default=-12.0)
    parser.add_argument("--min-snr", type=float, default=6.0)
    args = parser.parse_args()
    # Every calibration is an independent sweep. Do not begin from the value
    # saved by the previous run; otherwise a previous noisy result can bias
    # all subsequent measurements. The final selected value is still persisted
    # by set_mixer() and reapplied at service startup.
    gain = 0
    set_mixer(gain)
    stable = 0
    best_snr = None
    best_gain = gain
    print(f"Starting RMS receive calibration at {gain}/35 (forced reset); target {args.low}-{args.high}.", flush=True)
    for attempt in range(1, args.max_attempts + 1):
        levels, raw_peak, raw_rms, snr = run_attempt(args.target, args.callsign, args.timeout)
        if not levels:
            raw = f"; raw peak={raw_peak:.1f} dBFS, RMS={raw_rms:.1f} dBFS" if raw_peak is not None and raw_rms is not None else "; no raw meter sample"
            if raw_peak is not None and raw_peak > args.noise_high and gain > 0:
                gain -= 1
                set_mixer(gain)
                print(f"Attempt {attempt}: no Dire Wolf packet level received{raw}; noise floor is too hot, lowered DigiRig gain to {gain}/35.", flush=True)
            else:
                warning = " Raw level is noise-only; gain unchanged." if raw_peak is not None else ""
                print(f"Attempt {attempt}: no Dire Wolf packet level received{raw}; gain remains {gain}/35.{warning}", flush=True)
            continue
        level = levels[-1]
        raw = f"; raw peak={raw_peak:.1f} dBFS, RMS={raw_rms:.1f} dBFS" if raw_peak is not None and raw_rms is not None else "; no raw meter sample"
        snr_text = f"; S/N={snr:.1f} dB" if snr is not None else "; S/N unavailable"
        print(f"Attempt {attempt}: Dire Wolf levels={levels}{raw}{snr_text}; gain={gain}/35", flush=True)
        # Dire Wolf's packet audio level is a decoder confidence/level
        # display, not a linear ALSA meter. Use the peak of the exact PCM
        # stream forwarded to Dire Wolf for gain decisions.
        control_level = raw_peak if raw_peak is not None else float(level)
        in_range = (args.raw_low <= control_level <= args.raw_high) if raw_peak is not None else (args.low <= level <= args.high)
        stable = stable + 1 if in_range else 0
        if snr is not None and best_snr is not None and snr <= best_snr + 0.5:
            set_mixer(best_gain)
            print(f"S/N stopped improving; maximum useful gain is {best_gain}/35 at {best_snr:.1f} dB", flush=True)
            return 0
        if snr is not None and (best_snr is None or snr > best_snr):
            best_snr = snr
            best_gain = gain
        if snr is not None and snr < args.min_snr and gain < 35:
            gain += 1
            set_mixer(gain)
            print(f"S/N {snr:.1f} dB is below {args.min_snr:.1f} dB; raised DigiRig gain to {gain}/35", flush=True)
        elif raw_peak is not None and raw_peak < args.raw_low and gain < 35:
            gain += 1; set_mixer(gain); print(f"Raised DigiRig gain to {gain}/35 from raw peak {raw_peak:.1f} dBFS", flush=True)
        elif raw_peak is not None and raw_peak > args.raw_high and gain > 0:
            gain -= 1; set_mixer(gain); print(f"Lowered DigiRig gain to {gain}/35 from raw peak {raw_peak:.1f} dBFS", flush=True)
        elif raw_peak is None and level < args.low and gain < 35:
            gain += 1; set_mixer(gain); print(f"Raised DigiRig gain to {gain}/35", flush=True)
        elif raw_peak is None and level > args.high and gain > 0:
            gain -= 1; set_mixer(gain); print(f"Lowered DigiRig gain to {gain}/35", flush=True)
        if stable >= args.stable:
            measured = f"raw peak {raw_peak:.1f} dBFS" if raw_peak is not None else f"Dire Wolf level {level}"
            print(f"Calibration complete: final gain {gain}/35; stable {measured}", flush=True)
            return 0
    print(f"Calibration incomplete after {args.max_attempts} attempts; final gain {gain}/35", flush=True)
    return 2

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Calibration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
