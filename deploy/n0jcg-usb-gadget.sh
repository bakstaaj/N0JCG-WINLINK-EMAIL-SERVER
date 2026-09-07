#!/usr/bin/env bash
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/n0jcg
UDC="$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)"

if [[ -z "$UDC" ]]; then
    echo "N0JCG USB gadget: no USB device controller is available; use the Pi 4 USB-C power/data port." >&2
    exit 0
fi

modprobe configfs 2>/dev/null || true
modprobe libcomposite
mkdir -p /sys/kernel/config
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
mkdir -p /sys/kernel/config/usb_gadget
mkdir -p "$GADGET"
cd "$GADGET"

if [[ -n "$(cat UDC 2>/dev/null || true)" ]]; then
    echo "N0JCG USB gadget is already active."
    exit 0
fi

echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0200 > bcdUSB
echo 0x0100 > bcdDevice
mkdir -p strings/0x409
echo N0JCG > strings/0x409/manufacturer
echo PI-WINLINK > strings/0x409/product
echo 0123456789 > strings/0x409/serialnumber
mkdir -p configs/c.1/strings/0x409
echo "N0JCG Winlink USB network" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
mkdir -p functions/ecm.usb0
ln -sf functions/ecm.usb0 configs/c.1/
echo "$UDC" > UDC

for _ in {1..20}; do
    [[ -d /sys/class/net/usb0 ]] && break
    sleep 0.25
done

if command -v nmcli >/dev/null 2>&1 && nmcli connection show "n0jcg-usb-gadget" >/dev/null 2>&1; then
    nmcli connection up "n0jcg-usb-gadget" || true
else
    ip link set usb0 up || true
    ip addr replace 192.168.60.1/24 dev usb0 || true
fi
echo "N0JCG USB Ethernet gadget active at 192.168.60.1."
