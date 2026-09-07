#!/usr/bin/env bash
set -euo pipefail

USB_CONNECTION="n0jcg-usb-gadget"
USB_DEVICE="usb0"
USB_ADDRESS="192.168.60.1/24"

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
# only the Pi-side static address here. The Windows host must use a static
# address (192.168.60.2/24); this interface intentionally provides no DHCP.
nmcli connection down "$USB_CONNECTION" >/dev/null 2>&1 || true
nmcli connection modify "$USB_CONNECTION" ipv4.addresses "" ipv4.method disabled connection.autoconnect no >/dev/null 2>&1 || true
nmcli device set "$USB_DEVICE" managed no >/dev/null 2>&1 || true

ip link set "$USB_DEVICE" up
ip addr replace "$USB_ADDRESS" dev "$USB_DEVICE"

echo "N0JCG USB network: static Pi address is $USB_ADDRESS; configure the Windows host as 192.168.60.2/24. No USB DHCP service is enabled."
