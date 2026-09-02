from __future__ import annotations

import hashlib
import json
import platform
import secrets
from pathlib import Path

from n0jcg_licensing import LicenseClient

PRODUCT_ID = "winlink-email-appliance"
PRODUCT_NAME = "N0JCG Winlink Email Server"
LICENSE_PREFIX = "N0JCG-WLA-"
VERSION = "0.1.8"
DEFAULT_STATE = Path("/var/lib/n0jcg-winlink/registration.json")


def _client(path: Path) -> LicenseClient:
    return LicenseClient(product_slug=PRODUCT_ID, app_version=VERSION, state_root=path.parent / "license")


def registration_status(path: Path = DEFAULT_STATE) -> dict[str, object]:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    installation_id = str(saved.get("installation_id", ""))
    if not installation_id:
        installation_id = hashlib.sha256(f"{PRODUCT_ID}:{platform.node()}:{secrets.token_hex(16)}".encode()).hexdigest()[:24].upper()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"product_id": PRODUCT_ID, "installation_id": installation_id}, indent=2) + "\n", encoding="utf-8")
    license_status = _client(path).status()
    registered = bool(license_status.get("registered") or saved.get("license_token"))
    return {"product_name": PRODUCT_NAME, "product_id": PRODUCT_ID, "license_prefix": LICENSE_PREFIX, "installation_id": installation_id, "serial_number": license_status.get("serial_number"), "registered": registered, "mode": "registered" if registered else "trial", "license_configured": bool(license_status.get("license_configured")), "license_suffix": license_status.get("license_suffix", ""), "validation_error": license_status.get("validation_error")}


def activate(path: Path, license_serial: str, email: str) -> dict[str, object]:
    if not str(license_serial or "").strip().upper().startswith(LICENSE_PREFIX):
        raise ValueError(f"license S/N must start with {LICENSE_PREFIX}")
    _client(path).activate(license_serial, email)
    return registration_status(path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=PRODUCT_NAME + " registration utility")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/n0jcg-winlink/registration.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("id")
    sub.add_parser("status")
    request = sub.add_parser("create-request")
    request.add_argument("output", type=Path)
    activate_parser = sub.add_parser("activate")
    activate_parser.add_argument("license_serial")
    activate_parser.add_argument("email")
    args = parser.parse_args()
    if args.command == "id":
        print(registration_status(args.state)["serial_number"])
    elif args.command == "status":
        print(json.dumps(registration_status(args.state), indent=2))
    elif args.command == "create-request":
        payload = {"schema_version": 1, "product_id": PRODUCT_ID, "product_slug": PRODUCT_ID, "product_display_name": PRODUCT_NAME, "license_prefix": LICENSE_PREFIX, "installation_serial": registration_status(args.state)["serial_number"], "host_name": platform.node()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"PASS: registration request created at {args.output}")
    else:
        try:
            activate(args.state, args.license_serial, args.email)
        except Exception as exc:
            raise SystemExit(f"FAIL: {exc}")
        print(f"PASS: {PRODUCT_NAME} registered for {args.email}")
