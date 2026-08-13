#!/usr/bin/env python3
"""Dependency-free validation for the N0JCG Winlink appliance scaffold."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DIRS = ("assets/brand", "branding", "config", "docs", "tools", "deploy", "ui")


def main() -> int:
    failures = []
    for name in REQUIRED_DIRS:
        if not (ROOT / name).is_dir():
            failures.append(f"missing directory: {name}")

    example = ROOT / "config" / "appliance.example.json"
    try:
        data = json.loads(example.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - diagnostic path
        failures.append(f"invalid example configuration: {exc}")
        data = {}

    if data.get("product") != "N0JCG-WINLINK":
        failures.append("product must be N0JCG-WINLINK")
    if data.get("host_role") != "PI-WINLINK":
        failures.append("host_role must be PI-WINLINK")
    if data.get("product_display_name") != "N0JCG Winlink Email Server":
        failures.append("product_display_name must be N0JCG Winlink Email Server")

    platform = data.get("platform", {})
    if platform.get("board") != "Raspberry Pi 4 Model B":
        failures.append("platform board must be Raspberry Pi 4 Model B")
    if platform.get("architecture") != "aarch64":
        failures.append("platform architecture must be aarch64")

    radio = data.get("radio", {})
    if radio.get("transmit_enabled") is not False:
        failures.append("transmit_enabled must default to false")
    if not isinstance(radio.get("ptt_max_seconds"), int) or radio["ptt_max_seconds"] <= 0:
        failures.append("ptt_max_seconds must be a positive integer")

    required_files = (
        ROOT / "assets/brand/n0jcg-primary-light.svg",
        ROOT / "assets/brand/N0JCG_Header_Dark_Approved.png",
        ROOT / "branding/tokens.css",
        ROOT / "branding/N0JCG_Winlink_Email_Server_Product_Brief.md",
        ROOT / "ui/index.html",
        ROOT / "ui/styles.css",
        ROOT / "ui/webmail/index.html",
        ROOT / "ui/webmail/webmail.js",
        ROOT / "api/n0jcg_webmail.py",
        ROOT / "config/registration.example.json",
        ROOT / "tools/registration.py",
        ROOT / "deploy/setup_operator_auth.sh",
        ROOT / "docs/operator-authentication-and-registration.md",
        ROOT / "deploy/nginx/n0jcg-winlink.conf",
        ROOT / "deploy/install_static_ui.sh",
    )
    for path in required_files:
        if not path.is_file():
            failures.append(f"missing branding/UI file: {path.relative_to(ROOT)}")

    for path in (ROOT / "README.md", ROOT / "branding/README.md", ROOT / "ui/index.html"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if "N0JCG" not in content:
                failures.append(f"missing N0JCG brand text: {path.relative_to(ROOT)}")
            if "N0JCG Winlink Email Server" not in content:
                failures.append(f"missing visible product name: {path.relative_to(ROOT)}")

    registration_example = json.loads((ROOT / "config/registration.example.json").read_text(encoding="utf-8"))
    if registration_example.get("product_slug") != "winlink-email-appliance":
        failures.append("registration product_slug must be winlink-email-appliance")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: N0JCG-WINLINK scaffold validation")
    print(f"PASS: root={ROOT}")
    print("PASS: transmit default is disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
