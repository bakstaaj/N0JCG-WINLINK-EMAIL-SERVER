#!/usr/bin/env python3
"""Apply operator-controlled connectivity and Basic Auth settings as root."""
import json
import os
import pathlib
import re
import subprocess
import sys

CONFIG = pathlib.Path("/etc/n0jcg-winlink/network.conf")
AUTH_FILE = pathlib.Path("/etc/nginx/.htpasswd-n0jcg-winlink")
AUTH_SNIPPET = pathlib.Path("/etc/nginx/snippets/n0jcg-winlink-auth.conf.optional")
WIFI_CONNECTION = "n0jcg-wifi"
HOTSPOT_CONNECTION = "n0jcg-hotspot"


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def nm(*args, check=True):
    return run("/usr/bin/nmcli", *args, check=check)


def connection_exists(name):
    return nm("connection", "show", name, check=False).returncode == 0


def read_wifi_device():
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            if line.startswith("N0JCG_WIFI_DEVICE="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value
    result = nm("-t", "-f", "DEVICE,TYPE", "device", "status")
    for line in result.stdout.splitlines():
        device, _, kind = line.partition(":")
        if kind == "wifi":
            return device
    return ""


def validate(data):
    def text(name, default=""):
        value = data.get(name, default)
        return "" if value is None else str(value).strip()

    wifi_ssid = text("wifi_ssid")
    wifi_password = text("wifi_password")
    hotspot_ssid = text("hotspot_ssid") or "N0JCG-WES"
    hotspot_password = text("hotspot_password")
    operator_user = text("operator_user")
    operator_password = text("operator_password")
    for label, value, limit in (("Wi-Fi SSID", wifi_ssid, 32), ("hotspot SSID", hotspot_ssid, 32), ("operator username", operator_user, 64)):
        if len(value) > limit or any(ord(char) < 32 for char in value):
            raise ValueError(f"{label} is invalid")
    if wifi_ssid and len(wifi_password) < 8:
        raise ValueError("Wi-Fi password must be at least 8 characters")
    if hotspot_password and len(hotspot_password) < 8:
        raise ValueError("hotspot password must be at least 8 characters")
    if operator_user and (len(operator_user) < 1 or not re.fullmatch(r"[A-Za-z0-9._-]+", operator_user)):
        raise ValueError("operator username may contain only letters, numbers, dot, underscore, and hyphen")
    if operator_user and len(operator_password) < 8:
        raise ValueError("operator password must be at least 8 characters")
    return {
        "wifi_ssid": wifi_ssid, "wifi_password": wifi_password,
        "hotspot_ssid": hotspot_ssid, "hotspot_password": hotspot_password,
        "operator_user": operator_user, "operator_password": operator_password,
        "auto_hotspot": bool(data.get("auto_hotspot", True)),
        "usb_gadget": bool(data.get("usb_gadget", True)),
    }


def apply(settings):
    wifi_device = read_wifi_device()
    if settings["wifi_ssid"] and wifi_device:
        if not connection_exists(WIFI_CONNECTION):
            nm("connection", "add", "type", "wifi", "ifname", wifi_device, "con-name", WIFI_CONNECTION, "ssid", settings["wifi_ssid"])
        nm("connection", "modify", WIFI_CONNECTION, "802-11-wireless.ssid", settings["wifi_ssid"], "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", settings["wifi_password"], "connection.autoconnect", "yes")
        nm("connection", "up", WIFI_CONNECTION, "ifname", wifi_device, check=False)
    if connection_exists(HOTSPOT_CONNECTION):
        nm("connection", "modify", HOTSPOT_CONNECTION, "802-11-wireless.ssid", settings["hotspot_ssid"])
        if settings["hotspot_password"]:
            nm("connection", "modify", HOTSPOT_CONNECTION, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", settings["hotspot_password"])
        nm("connection", "modify", HOTSPOT_CONNECTION, "connection.autoconnect", "no")
    CONFIG.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    CONFIG.write_text("\n".join((
        f"N0JCG_AP_SSID={settings['hotspot_ssid']!r}",
        f"N0JCG_WIFI_DEVICE={wifi_device!r}",
        f"N0JCG_WIFI_SSID={settings['wifi_ssid']!r}",
        "N0JCG_NETWORK_BACKEND=networkmanager",
        "N0JCG_AP_ADDRESS=192.168.50.1/24",
        "N0JCG_AP_DHCP_RANGE=192.168.50.100,192.168.50.200",
        "N0JCG_USB_ADDRESS=192.168.60.1/24",
        f"N0JCG_AUTO_HOTSPOT={'1' if settings['auto_hotspot'] else '0'}",
        "")), encoding="utf-8")
    os.chmod(CONFIG, 0o600)
    if settings["auto_hotspot"]:
        run("/usr/bin/systemctl", "enable", "--now", "n0jcg-network-fallback.service", check=False)
    else:
        run("/usr/bin/systemctl", "disable", "--now", "n0jcg-network-fallback.service", check=False)
        nm("connection", "down", HOTSPOT_CONNECTION, check=False)
    gadget_action = "enable" if settings["usb_gadget"] else "disable"
    run("/usr/bin/systemctl", gadget_action, "--now", "n0jcg-usb-gadget.service", check=False)
    if settings["operator_user"]:
        AUTH_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        # Pass the password on stdin; never place it in the command arguments.
        subprocess.run(["/usr/bin/htpasswd", "-iB", "-c", str(AUTH_FILE), settings["operator_user"]], input=settings["operator_password"] + "\n", text=True, check=True, stdout=subprocess.DEVNULL)
        os.chmod(AUTH_FILE, 0o640)
        try:
            import grp
            os.chown(AUTH_FILE, 0, grp.getgrnam("www-data").gr_gid)
        except (KeyError, PermissionError):
            pass
        AUTH_SNIPPET.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        AUTH_SNIPPET.write_text('auth_basic "N0JCG Winlink Email Server operator console";\nauth_basic_user_file /etc/nginx/.htpasswd-n0jcg-winlink;\n', encoding="utf-8")
        run("/usr/sbin/nginx", "-t")
        run("/usr/bin/systemctl", "reload", "nginx")


def main():
    if os.geteuid() != 0:
        raise SystemExit("FAIL: run as root")
    request = pathlib.Path(sys.argv[1])
    try:
        settings = validate(json.loads(request.read_text(encoding="utf-8")))
        apply(settings)
    finally:
        request.unlink(missing_ok=True)
    print("PASS: operator settings applied")


if __name__ == "__main__":
    main()
