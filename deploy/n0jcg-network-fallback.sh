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

if [[ "${N0JCG_AUTO_HOTSPOT:-1}" != "1" ]]; then
    nmcli connection down "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
    exit 0
fi

if [[ -z "$WIFI_DEVICE" ]]; then
    WIFI_DEVICE="$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }')"
fi

if [[ -z "$WIFI_DEVICE" ]]; then
    WIFI_DEVICE="wlan0"
fi

wifi_is_ready() {
    # Connectivity setup deliberately defaults to hotspot-first. An older
    # config without this key is treated the same way until the operator
    # explicitly enables local Wi-Fi in the console.
    [[ "${N0JCG_WIFI_DISABLED:-1}" != "1" ]] || return 1
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
        # Keep the live NetworkManager profile aligned with the operator's
        # saved configuration before bringing the fallback AP up. This also
        # repairs profiles created by older installers that still advertise
        # the original default SSID.
        if [[ -n "${N0JCG_AP_SSID:-}" ]]; then
            nmcli connection modify "$HOTSPOT_CONNECTION" 802-11-wireless.ssid "$N0JCG_AP_SSID" >/dev/null 2>&1 || true
        fi
        # Do not re-activate an already-running AP. Repeated activation
        # tears down the beacon and DHCP process and prevents clients from
        # completing association/authentication.
        if nmcli -t -f NAME,DEVICE connection show --active | grep -Fqx "$HOTSPOT_CONNECTION:$WIFI_DEVICE"; then
            return 0
        fi
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
