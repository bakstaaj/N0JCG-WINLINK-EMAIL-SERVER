"""Winlink RMS gateway cache and location helpers.

The live source is the Winlink gateway status API.  The appliance stores the
last successful response locally so an operator can still choose a gateway
without Internet access in the field.
"""

import json
import errno
import math
import os
import subprocess
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


STATE_DIR = Path(os.environ.get("N0JCG_WEBMAIL_STATE_DIR", "/var/lib/n0jcg-winlink-webmail"))
CACHE_PATH = STATE_DIR / "rms-gateways.json"
LOCATION_PATH = STATE_DIR / "location.json"
STATUS_URL = os.environ.get("N0JCG_RMS_STATUS_URL", "https://api.winlink.org/gateway/status.json")
# This is the public API access key used by Pat's RMS status client. Operators
# may replace it through the environment without changing application code.
STATUS_ACCESS_KEY = os.environ.get("N0JCG_RMS_STATUS_ACCESS_KEY", "1880278F11684B358F36845615BD039A")
GPS_GUARD = "/usr/local/sbin/n0jcg-gps-rf-guard"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or ""))
        return float(match.group(0)) if match else None


def _first(item, *keys):
    for key in keys:
        if isinstance(item, dict) and item.get(key) not in (None, ""):
            return item[key]
    return ""


def _channels(item):
    channels = _first(item, "Channels", "channels", "GatewayChannels", "gatewayChannels", "Channel", "channelsList")
    if isinstance(channels, dict):
        channels = [channels]
    return channels if isinstance(channels, list) else []


def _gateway_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("Gateways", "gateways", "Gateway", "gatewayStatus", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    found = []
    def walk(value):
        if isinstance(value, dict):
            if _first(value, "Callsign", "callsign", "BaseCallsign", "baseCallsign") and _channels(value):
                found.append(value)
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    return found


def normalize_gateways(payload):
    """Convert Winlink API variants into stable gateway/channel records."""
    records = []
    for item in _gateway_items(payload):
        callsign = str(_first(item, "Callsign", "callsign", "BaseCallsign", "baseCallsign")).upper()
        latitude = _number(_first(item, "Latitude", "latitude", "Lat", "lat"))
        longitude = _number(_first(item, "Longitude", "longitude", "Lon", "lon", "Lng", "lng"))
        if not callsign or latitude is None or longitude is None:
            continue
        gateway_status = _first(item, "LastStatus", "lastStatus", "Status", "status")
        for channel in _channels(item) or [{}]:
            frequency = _number(_first(channel, "Frequency", "frequency", "Freq", "freq"))
            if frequency is None:
                continue
            mode = str(_first(channel, "SupportedModes", "supportedModes", "Mode", "mode", "RequestedMode", "requestedMode") or _first(item, "RequestedMode", "requestedMode") or "").upper()
            baud = _number(_first(channel, "Baud", "baud", "BaudRate", "baudRate"))
            service = str(_first(channel, "ServiceCode", "serviceCode", "Service", "service") or _first(item, "ServiceCode", "serviceCode") or "PUBLIC").upper()
            digipeater = str(_first(channel, "Digipeater", "digipeater", "DigipeaterCallsign", "digipeaterCallsign", "Via", "via") or _first(item, "Digipeater", "digipeater", "DigipeaterCallsign", "digipeaterCallsign", "Via", "via") or "").upper()
            record_type = "DIGI" if "DIGI" in service or str(_first(channel, "Type", "type", "StationType", "stationType") or "").upper() in {"DIGI", "DIGIPEATER"} else "RMS"
            records.append({
                "callsign": callsign,
                "name": str(_first(item, "Name", "name", "Comments", "comments") or ""),
                "latitude": latitude,
                "longitude": longitude,
                "frequency_mhz": frequency / 1000000 if frequency > 1000 else frequency,
                "mode": mode or "PACKET",
                "baud": int(baud) if baud is not None else None,
                "service_code": service,
                "type": record_type,
                "digipeater": digipeater,
                "rms_target": callsign,
                "last_status": gateway_status,
            })
    return records


def _distance_bearing(origin_lat, origin_lon, target_lat, target_lon):
    radius_miles = 3958.7613
    lat1, lat2 = math.radians(origin_lat), math.radians(target_lat)
    delta_lat = math.radians(target_lat - origin_lat)
    delta_lon = math.radians(target_lon - origin_lon)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    distance = radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))
    bearing = (math.degrees(math.atan2(math.sin(delta_lon) * math.cos(lat2), math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon))) + 360) % 360
    return round(distance, 1), round(bearing, 1)


def enrich_nearest(records, latitude, longitude, limit=25, mode="packet"):
    selected = []
    for record in records:
        if mode and mode.lower() == "packet" and not (record.get("baud") == 1200 or "1200" in record["mode"] or "AFSK" in record["mode"]):
            continue
        distance, bearing = _distance_bearing(latitude, longitude, record["latitude"], record["longitude"])
        selected.append({**record, "distance_miles": distance, "bearing_degrees": bearing})
    return sorted(selected, key=lambda value: (value["distance_miles"], value["callsign"]))[:limit]


def _read_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def cache_payload():
    payload = _read_json(CACHE_PATH)
    if not isinstance(payload, dict):
        return {"installed": False, "records": [], "updated_at": None, "source": STATUS_URL}
    return payload


def refresh_cache(timeout=20):
    query = urllib.parse.urlencode({"key": STATUS_ACCESS_KEY, "mode": "Packet", "HistoryHours": "48", "ServiceCodes": "PUBLIC"})
    request = urllib.request.Request(f"{STATUS_URL}?{query}", headers={"User-Agent": "N0JCG-Winlink-Email-Server/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            source_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, OSError) and exc.reason.errno == errno.EBUSY:
            raise RuntimeError("Internet connection is unavailable; the cached RMS gateway list was not changed.") from exc
        raise
    records = normalize_gateways(source_payload)
    if not records:
        raise RuntimeError("Winlink gateway list contained no usable Packet channels")
    envelope = {"installed": True, "records": records, "updated_at": int(time.time()), "source": STATUS_URL, "count": len(records)}
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")
    os.chmod(CACHE_PATH, 0o600)
    return envelope


def _read_gpsd():
    started = False
    try:
        started = subprocess.run(["sudo", "-n", GPS_GUARD, "start"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        # gpspipe's first record is normally VERSION/DEVICES/WATCH metadata;
        # read several reports so a TPV position can actually be observed.
        result = subprocess.run(["gpspipe", "-w", "-n", "10"], capture_output=True, text=True, timeout=6)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    finally:
        if started:
            try:
                subprocess.run(["sudo", "-n", GPS_GUARD, "stop"], capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("class") == "TPV" and value.get("mode", 0) >= 2:
            latitude, longitude = _number(value.get("lat")), _number(value.get("lon"))
            if latitude is not None and longitude is not None:
                return {"source": "gps", "latitude": latitude, "longitude": longitude, "observed_at": int(time.time())}
    return None


def location_state():
    configured = _read_json(LOCATION_PATH) or {"source": "unavailable", "latitude": None, "longitude": None}
    if configured.get("source") == "simulated" and configured.get("latitude") is not None:
        return configured
    fix = _read_gpsd()
    if fix:
        return fix
    return {**configured, "source": "unavailable", "message": "No GPS fix and no simulated coordinates are enabled."}


def set_simulated_location(latitude, longitude, enabled):
    latitude, longitude = _number(latitude), _number(longitude)
    if enabled and (latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        raise ValueError("enter valid simulated latitude and longitude")
    value = {"source": "simulated" if enabled else "gps", "latitude": latitude, "longitude": longitude, "observed_at": int(time.time())}
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    LOCATION_PATH.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.chmod(LOCATION_PATH, 0o600)
    return location_state()
