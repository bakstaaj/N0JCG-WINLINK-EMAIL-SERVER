#!/usr/bin/env bash
set -euo pipefail

WIFI_CONNECTION="n0jcg-wifi"
HOTSPOT_CONNECTION="n0jcg-hotspot"
WIFI_DEVICE="wlan0"

wifi_is_ready() {
    nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep -q "^${WIFI_CONNECTION}:${WIFI_DEVICE}$" && \
        ip -4 addr show dev "$WIFI_DEVICE" 2>/dev/null | grep -q 'inet '
}

ensure_network_state() {
    if wifi_is_ready; then
        nmcli connection down "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
        return 0
    fi
    if nmcli connection show "$HOTSPOT_CONNECTION" >/dev/null 2>&1; then
        nmcli connection up "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
    fi
}

if [[ "${1:-}" == "--watch" ]]; then
    sleep 20
    while true; do
        ensure_network_state
        sleep 15
    done
fi

ensure_network_state
