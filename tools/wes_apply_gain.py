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
subprocess.run(["/usr/bin/amixer", "-c", "Device", "cset", "numid=8", str(gain)], check=True)
