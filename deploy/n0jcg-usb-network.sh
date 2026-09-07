#!/usr/bin/env bash
set -euo pipefail

USB_CONNECTION="n0jcg-usb-gadget"
USB_DEVICE="usb0"
USB_ADDRESS="192.168.60.1/24"

for _ in {1..30}; do
    if ip link show dev "$USB_DEVICE" >/dev/null 2>&1; then
        ip link set "$USB_DEVICE" up || true
        if nmcli connection up "$USB_CONNECTION" ifname "$USB_DEVICE" >/dev/null 2>&1 &&
           ip -4 addr show dev "$USB_DEVICE" | grep -q '192\.168\.60\.1/24'; then
            echo "N0JCG USB network active at $USB_ADDRESS."
            exit 0
        fi
    fi
    sleep 1
done

ip link set "$USB_DEVICE" up || true
ip addr replace "$USB_ADDRESS" dev "$USB_DEVICE" || true
echo "N0JCG USB network: NetworkManager shared activation is pending." >&2
exit 1
