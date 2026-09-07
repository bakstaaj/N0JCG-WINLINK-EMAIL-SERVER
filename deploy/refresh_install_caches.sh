#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${N0JCG_APP_ROOT:-/opt/n0jcg-winlink}"
STATE_DIR="${N0JCG_WEBMAIL_STATE_DIR:-/var/lib/n0jcg-winlink-webmail}"
APP_USER="${N0JCG_APP_USER:-pi}"

mkdir -p "$STATE_DIR"
chown "$APP_USER:$APP_USER" "$STATE_DIR" 2>/dev/null || true

if ! command -v curl >/dev/null 2>&1 || ! curl --silent --show-error --connect-timeout 3 --max-time 5 --output /dev/null https://api.winlink.org/; then
    echo "INFO: internet unavailable; skipping RMS gateway and Standard Forms downloads"
    exit 0
fi

echo "Downloading the current Winlink RMS gateway list..."
if PYTHONPATH="$APP_ROOT/api${PYTHONPATH:+:$PYTHONPATH}" \
    N0JCG_WEBMAIL_STATE_DIR="$STATE_DIR" \
    python3 -c 'from rms_gateways import refresh_cache; result = refresh_cache(); print("PASS: cached {} RMS gateway channels".format(result.get("count", 0)))'; then
    :
else
    echo "WARN: RMS gateway list could not be refreshed; any existing cache was preserved." >&2
fi

echo "Downloading the current Winlink Standard Forms library..."
if N0JCG_TEMPLATES_OWNER="$APP_USER" \
    N0JCG_TEMPLATES_DIR="$STATE_DIR/templates" \
    python3 "$APP_ROOT/api/winlink_templates.py"; then
    echo "PASS: Standard Forms cache refreshed"
else
    echo "WARN: Standard Forms could not be refreshed; any existing cache was preserved." >&2
fi
