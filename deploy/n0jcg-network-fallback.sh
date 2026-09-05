#!/usr/bin/env bash
set -euo pipefail

WIFI_CONNECTION="n0jcg-wifi"
HOTSPOT_CONNECTION="n0jcg-hotspot"
CONFIG_FILE="/etc/n0jcg-winlink/network.conf"
WIFI_DEVICE="${N0JCG_WIFI_DEVICE:-}"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1091
    source "$CONFIG_FILE"
fi

if [[ -z "$WIFI_DEVICE" ]]; then
    WIFI_DEVICE="$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }')"
fi

if [[ -z "$WIFI_DEVICE" ]]; then
    WIFI_DEVICE="wlan0"
fi

wifi_is_ready() {
    [[ -n "$WIFI_DEVICE" ]] && \
        ip link show dev "$WIFI_DEVICE" >/dev/null 2>&1 && \
        ip -4 addr show dev "$WIFI_DEVICE" 2>/dev/null | grep -q 'inet ' && \
        ip route show default dev "$WIFI_DEVICE" 2>/dev/null | grep -q '^default '
}

ensure_network_state() {
    if wifi_is_ready; then
        nmcli connection down "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
        return 0
    fi
    if nmcli connection show "$HOTSPOT_CONNECTION" >/dev/null 2>&1; then
        nmcli connection up "$HOTSPOT_CONNECTION" ifname "$WIFI_DEVICE" >/dev/null 2>&1 || true
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
