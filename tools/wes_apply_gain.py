#!/usr/bin/env python3
"""Apply the persisted WES DigiRig capture gain at service startup."""
from pathlib import Path
import subprocess

path = Path("/var/lib/n0jcg-winlink-webmail/digirig-gain")
try:
    gain = int(path.read_text(encoding="ascii").strip())
except (OSError, ValueError):
    gain = 3
gain = max(0, min(35, gain))
try:
    card = subprocess.check_output(["/usr/local/sbin/n0jcg-wes-audio-device", "--card"], text=True).strip()
    subprocess.run(["/usr/bin/amixer", "-c", card, "cset", "numid=8", str(gain)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
except (OSError, subprocess.CalledProcessError):
    # Mixer control is optional. Never prevent Dire Wolf from starting when a
    # USB audio card is temporarily absent or has no Mic Capture control.
    pass
