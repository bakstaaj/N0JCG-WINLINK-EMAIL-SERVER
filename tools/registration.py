#!/usr/bin/env python3
"""Create and inspect the local N0JCG Winlink Email Server registration record.

This is the appliance-side registration foundation. It deliberately does not
enable RF transmission and does not treat a registration record as hardware
verification. A future signed activation response can be validated at the
marked boundary without changing the UI or state model.
"""

import argparse
import hashlib
import json
import os
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path


PRODUCT_ID = "N0JCG-WINLINK-EMAIL-SERVER"
PRODUCT_NAME = "N0JCG Winlink Email Server"
DEFAULT_STATE = Path(os.environ.get("N0JCG_REGISTRATION_STATE", "/var/lib/n0jcg-winlink/registration.json"))


def read_machine_id() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
    return f"{socket.gethostname()}:{platform.machine()}"


def device_id(machine_id: str) -> str:
    digest = hashlib.sha256(f"{PRODUCT_ID}:{machine_id}".encode("utf-8")).hexdigest().upper()
    return f"N0JCG-{digest[:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:16]}"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state(path: Path, current_device_id: str) -> dict:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {
            "schema_version": 1,
            "product_id": PRODUCT_ID,
            "product_display_name": PRODUCT_NAME,
            "registration_status": "unregistered",
            "device_id": current_device_id,
            "registration_request": None,
            "license_id": None,
            "registered_to": None,
            "activated_at": None,
            "expires_at": None,
            "entitlements": [],
            "verification": "pending",
        }
    data["device_id"] = data.get("device_id") or current_device_id
    return data


def write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=PRODUCT_NAME + " registration utility")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--machine-id", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("id", help="print the stable product device ID")
    sub.add_parser("status", help="print the local registration state")
    request = sub.add_parser("create-request", help="write a registration request JSON file")
    request.add_argument("output", type=Path)
    args = parser.parse_args()

    current_device_id = device_id(args.machine_id or read_machine_id())
    if args.command == "id":
        print(current_device_id)
        return 0

    state = load_state(args.state, current_device_id)
    if args.command == "status":
        print(json.dumps(state, indent=2))
        return 0

    request_data = {
        "schema_version": 1,
        "product_id": PRODUCT_ID,
        "product_display_name": PRODUCT_NAME,
        "device_id": current_device_id,
        "host_name": socket.gethostname(),
        "architecture": platform.machine(),
        "requested_at": now(),
        "registration_status": "request_created",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request_data, indent=2) + "\n", encoding="utf-8")
    state["registration_request"] = str(args.output)
    state["registration_status"] = "request_created"
    state["verification"] = "pending"
    write_state(args.state, state)
    print(f"PASS: registration request created at {args.output}")
    print(f"DEVICE_ID: {current_device_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

