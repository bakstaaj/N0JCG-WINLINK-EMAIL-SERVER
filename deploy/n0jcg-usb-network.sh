#!/usr/bin/env bash
set -euo pipefail

USB_CONNECTION="n0jcg-usb-gadget"
USB_DEVICE="usb0"
USB_ADDRESS="192.168.60.1/24"
DHCP_RANGE="192.168.60.100,192.168.60.200,255.255.255.0,12h"

for _ in {1..30}; do
    ip link show dev "$USB_DEVICE" >/dev/null 2>&1 && break
    sleep 1
done
ip link show dev "$USB_DEVICE" >/dev/null 2>&1 || {
    echo "N0JCG USB network: $USB_DEVICE was not created by the gadget service." >&2
    exit 1
}

# NetworkManager's shared profile can report carrier while the RNDIS netdev is
# not actually lower-up. Keep NetworkManager away from this interface and own
# the static peer address and DHCP process here instead.
nmcli connection down "$USB_CONNECTION" >/dev/null 2>&1 || true
nmcli connection modify "$USB_CONNECTION" ipv4.method disabled connection.autoconnect no >/dev/null 2>&1 || true
nmcli device set "$USB_DEVICE" managed no >/dev/null 2>&1 || true

ip link set "$USB_DEVICE" up
ip addr replace "$USB_ADDRESS" dev "$USB_DEVICE"

exec dnsmasq \
    --no-daemon \
    --keep-in-foreground \
    --interface="$USB_DEVICE" \
    --bind-interfaces \
    --port=0 \
    --dhcp-authoritative \
    --dhcp-range="$DHCP_RANGE" \
    --dhcp-option="3,192.168.60.1" \
    --dhcp-option="6,192.168.60.1"
