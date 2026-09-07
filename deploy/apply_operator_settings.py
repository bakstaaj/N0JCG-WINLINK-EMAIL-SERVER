#!/usr/bin/env python3
"""Apply operator-controlled connectivity or Basic Auth settings as root."""
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

CONFIG = pathlib.Path("/etc/n0jcg-winlink/network.conf")
AUTH_FILE = pathlib.Path("/etc/nginx/.htpasswd-n0jcg-winlink")
AUTH_SNIPPET = pathlib.Path("/etc/nginx/snippets/n0jcg-winlink-auth.conf.optional")
OPERATOR_META = pathlib.Path("/etc/n0jcg-winlink/operator.conf")
WIFI_CONNECTION = "n0jcg-wifi"
HOTSPOT_CONNECTION = "n0jcg-hotspot"


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def nm(*args, check=True):
    return run("/usr/bin/nmcli", *args, check=check)


def connection_exists(name):
    return nm("connection", "show", name, check=False).returncode == 0


def connection_active(name):
    result = nm("-t", "-f", "NAME", "connection", "show", "--active", check=False)
    if result.returncode != 0:
        return False
    return any(line.strip() == name for line in result.stdout.splitlines())


def read_config():
    values = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip("'\"")
    return values


def read_wifi_device(config):
    if config.get("N0JCG_WIFI_DEVICE"):
        return config["N0JCG_WIFI_DEVICE"]
    result = nm("-t", "-f", "DEVICE,TYPE", "device", "status")
    for line in result.stdout.splitlines():
        device, _, kind = line.partition(":")
        if kind == "wifi":
            return device
    return ""


def read_active_wifi_ssid():
    """Read the active Wi-Fi SSID for upgrades with incomplete network.conf."""
    result = nm("-t", "-f", "DEVICE,TYPE,CONNECTION", "device", "status", check=False)
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or fields[1] != "wifi" or fields[2] == "--":
            continue
        profile = nm("-g", "802-11-wireless.ssid", "connection", "show", fields[2], check=False)
        if profile.returncode == 0 and profile.stdout.strip():
            return profile.stdout.strip()
    return ""


def validate(data, operation, current):
    def text(name):
        value = data.get(name)
        return "" if value is None else str(value).strip()

    if operation == "network":
        wifi_ssid, wifi_password = text("wifi_ssid"), text("wifi_password")
        hotspot_ssid, hotspot_password = text("hotspot_ssid"), text("hotspot_password")
        current_wifi_ssid = current.get("N0JCG_WIFI_SSID", "") or read_active_wifi_ssid()
        if wifi_ssid and wifi_ssid != current_wifi_ssid and len(wifi_password) < 8:
            raise ValueError("Wi-Fi password is required when changing the Wi-Fi SSID")
        if hotspot_password and len(hotspot_password) < 8:
            raise ValueError("hotspot password must be at least 8 characters")
        for label, value in (("Wi-Fi SSID", wifi_ssid), ("hotspot SSID", hotspot_ssid)):
            if len(value) > 32 or any(ord(char) < 32 for char in value):
                raise ValueError(f"{label} is invalid")
        return {"operation": operation, "wifi_ssid": wifi_ssid, "wifi_password": wifi_password, "hotspot_ssid": hotspot_ssid, "hotspot_password": hotspot_password, "auto_hotspot": data.get("auto_hotspot"), "disable_wifi": data.get("disable_wifi"), "usb_gadget": data.get("usb_gadget")}
    if operation == "auth":
        operator_user, operator_password = text("operator_user"), text("operator_password")
        if operator_user and not re.fullmatch(r"[A-Za-z0-9._-]+", operator_user):
            raise ValueError("operator username may contain only letters, numbers, dot, underscore, and hyphen")
        if operator_user and operator_user != current.get("N0JCG_OPERATOR_USER", "") and len(operator_password) < 8:
            raise ValueError("operator password is required when changing the operator username")
        if operator_password and len(operator_password) < 8:
            raise ValueError("operator password must be at least 8 characters")
        return {"operation": operation, "operator_user": operator_user, "operator_password": operator_password}
    raise ValueError("unknown operator settings operation")


def write_config(values):
    merged = read_config()
    merged.update(values)
    CONFIG.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    lines = [f"{key}={shlex.quote(value)}" for key, value in merged.items() if key.startswith("N0JCG_")]
    CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(CONFIG, 0o600)


def apply_network(settings):
    current = read_config()
    wifi_device = read_wifi_device(current)
    wifi_ssid = settings["wifi_ssid"] or current.get("N0JCG_WIFI_SSID", "")
    current_hotspot_ssid = current.get("N0JCG_AP_SSID", "N0JCG-WES")
    hotspot_changed = bool(settings["hotspot_ssid"] and settings["hotspot_ssid"] != current_hotspot_ssid)
    wifi_disabled = settings["disable_wifi"] is True
    wifi_connection = current.get("N0JCG_WIFI_CONNECTION", "")
    if settings["wifi_ssid"] and wifi_device and not wifi_disabled:
        if not connection_exists(WIFI_CONNECTION):
            nm("connection", "add", "type", "wifi", "ifname", wifi_device, "con-name", WIFI_CONNECTION, "ssid", wifi_ssid)
        if settings["wifi_password"]:
            nm("connection", "modify", WIFI_CONNECTION, "802-11-wireless.ssid", wifi_ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", settings["wifi_password"], "connection.autoconnect", "yes")
        else:
            nm("connection", "modify", WIFI_CONNECTION, "802-11-wireless.ssid", wifi_ssid, "connection.autoconnect", "yes")
        nm("connection", "up", WIFI_CONNECTION, "ifname", wifi_device, check=False)
    hotspot_was_active = connection_exists(HOTSPOT_CONNECTION) and connection_active(HOTSPOT_CONNECTION)
    if connection_exists(HOTSPOT_CONNECTION):
        hotspot_ssid = settings["hotspot_ssid"] or current_hotspot_ssid
        nm("connection", "modify", HOTSPOT_CONNECTION, "802-11-wireless.ssid", hotspot_ssid)
        if settings["hotspot_password"]:
            nm("connection", "modify", HOTSPOT_CONNECTION, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", settings["hotspot_password"])
        nm("connection", "modify", HOTSPOT_CONNECTION, "connection.autoconnect", "no")
    values = {"N0JCG_WIFI_DEVICE": wifi_device, "N0JCG_WIFI_SSID": wifi_ssid, "N0JCG_NETWORK_BACKEND": "networkmanager"}
    if settings["hotspot_ssid"]:
        values["N0JCG_AP_SSID"] = settings["hotspot_ssid"]
    if settings["auto_hotspot"] is not None:
        values["N0JCG_AUTO_HOTSPOT"] = "1" if settings["auto_hotspot"] else "0"
    if settings["disable_wifi"] is not None:
        values["N0JCG_WIFI_DISABLED"] = "1" if wifi_disabled else "0"
    write_config(values)
    # Make the profile change visible to NetworkManager before cycling an
    # active AP. This matters on installs where the fallback service is
    # already watching the connection.
    nm("connection", "reload", check=False)
    if wifi_disabled and wifi_device:
        active = nm("-g", "GENERAL.CONNECTION", "device", "show", wifi_device, check=False).stdout.strip()
        if active and active != "--":
            wifi_connection = active
            nm("connection", "modify", active, "connection.autoconnect", "no", check=False)
        if wifi_connection:
            values["N0JCG_WIFI_CONNECTION"] = wifi_connection
            write_config(values)
        nm("radio", "wifi", "on", check=False)
        nm("device", "disconnect", wifi_device, check=False)
        nm("connection", "modify", HOTSPOT_CONNECTION, "connection.autoconnect", "yes", check=False)
        nm("connection", "down", HOTSPOT_CONNECTION, check=False)
        nm("connection", "up", HOTSPOT_CONNECTION, "ifname", wifi_device, check=False)
        run("/usr/bin/systemctl", "restart", "n0jcg-network-fallback.service", check=False)
    elif settings["disable_wifi"] is False and wifi_device:
        if wifi_connection:
            nm("connection", "modify", wifi_connection, "connection.autoconnect", "yes", check=False)
        nm("connection", "modify", HOTSPOT_CONNECTION, "connection.autoconnect", "no", check=False)
        nm("device", "connect", wifi_device, check=False)
        run("/usr/bin/systemctl", "restart", "n0jcg-network-fallback.service", check=False)
    if settings["auto_hotspot"] is True:
        run("/usr/bin/systemctl", "enable", "--now", "n0jcg-network-fallback.service", check=False)
        if hotspot_changed and hotspot_was_active:
            # NetworkManager does not renegotiate an active AP profile after
            # `connection modify`; cycle it so clients see the new SSID.
            nm("connection", "down", HOTSPOT_CONNECTION, check=False)
            nm("connection", "up", HOTSPOT_CONNECTION, check=False)
    elif settings["auto_hotspot"] is False:
        run("/usr/bin/systemctl", "disable", "--now", "n0jcg-network-fallback.service", check=False)
        nm("connection", "down", HOTSPOT_CONNECTION, check=False)
    if settings["usb_gadget"] is True:
        run("/usr/bin/systemctl", "enable", "--now", "n0jcg-usb-gadget.service", check=False)
    elif settings["usb_gadget"] is False:
        run("/usr/bin/systemctl", "disable", "--now", "n0jcg-usb-gadget.service", check=False)


def read_current_user():
    if OPERATOR_META.exists():
        for line in OPERATOR_META.read_text(encoding="utf-8").splitlines():
            if line.startswith("N0JCG_OPERATOR_USER="):
                return line.split("=", 1)[1].strip().strip("'\"")
    if AUTH_FILE.exists():
        lines = AUTH_FILE.read_text(encoding="utf-8").splitlines()
        if lines and ":" in lines[0]:
            return lines[0].split(":", 1)[0]
    return ""


def apply_auth(settings):
    if not settings["operator_password"]:
        return
    user = settings["operator_user"] or read_current_user()
    if not user:
        raise ValueError("operator username is required when setting a password")
    AUTH_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/htpasswd", "-iB", "-c", str(AUTH_FILE), user], input=settings["operator_password"] + "\n", text=True, check=True, stdout=subprocess.DEVNULL)
    os.chmod(AUTH_FILE, 0o640)
    AUTH_SNIPPET.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    AUTH_SNIPPET.write_text('auth_basic "N0JCG Winlink Email Server operator console";\nauth_basic_user_file /etc/nginx/.htpasswd-n0jcg-winlink;\n', encoding="utf-8")
    OPERATOR_META.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    OPERATOR_META.write_text(f"N0JCG_OPERATOR_USER={shlex.quote(user)}\n", encoding="utf-8")
    os.chmod(OPERATOR_META, 0o644)
    run("/usr/sbin/nginx", "-t")
    run("/usr/bin/systemctl", "reload", "nginx")


def main():
    if os.geteuid() != 0:
        raise SystemExit("FAIL: run as root")
    request = pathlib.Path(sys.argv[1])
    try:
        data = json.loads(request.read_text(encoding="utf-8"))
        operation = str(data.get("operation") or "network")
        current = read_config()
        current["N0JCG_OPERATOR_USER"] = read_current_user()
        settings = validate(data, operation, current)
        apply_network(settings) if operation == "network" else apply_auth(settings)
    finally:
        request.unlink(missing_ok=True)
    print("PASS: operator settings applied")


if __name__ == "__main__":
    main()
