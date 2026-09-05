#!/usr/bin/env python3
"""Resolve the live ALSA capture card used by the DigiRig."""
from __future__ import annotations

import os
import re
import subprocess
import sys

PROFILE = "/var/lib/n0jcg-winlink-webmail/radio-profile.conf"
CARD_RE = re.compile(r"^card\s+(\d+):\s+([^\[]+)\s+\[(.+)\]", re.MULTILINE)


def configured_device() -> str:
    try:
        for line in open(PROFILE, encoding="ascii"):
            if line.startswith("N0JCG_AUDIO_DEVICE="):
                return line.rstrip("\n").split("=", 1)[1]
    except OSError:
        pass
    return os.environ.get("N0JCG_AUDIO_DEVICE", "")


def arecord_list() -> str:
    result = subprocess.run(["/usr/bin/arecord", "-l"], capture_output=True, text=True)
    return result.stdout


def resolve() -> tuple[str, str]:
    listing = arecord_list()
    configured = configured_device()
    # Preserve the operator's choice when its card is currently enumerated.
    if configured and configured.startswith(("hw:", "plughw:")):
        card = configured.split(":", 1)[1].rsplit(",", 1)[0]
        if any((f"card {card}:" in line or f"{card} [" in line) for line in listing.splitlines()):
            return configured, card
    cards = CARD_RE.findall(listing)
    if not cards:
        raise RuntimeError("no ALSA capture cards are currently available")
    # Prefer USB/DigiRig-style names, otherwise use the first capture card.
    preferred = next((item for item in cards if re.search(r"usb|digi|cm108|c-media|audio", " ".join(item), re.I)), cards[0])
    return f"plughw:{preferred[0]},0", preferred[0]


def main() -> int:
    device, card = resolve()
    if len(sys.argv) > 1 and sys.argv[1] == "--card":
        print(card)
    else:
        print(device)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"WES audio device unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1)
