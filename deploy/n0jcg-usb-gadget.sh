#!/usr/bin/env bash
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/n0jcg
UDC=""
for candidate in /sys/class/udc/*xhci2-controller; do
    if [[ -e "$candidate" ]]; then
        UDC="$(basename "$candidate")"
        break
    fi
done
if [[ -z "$UDC" ]]; then
    UDC="$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)"
fi

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
    if [[ -e os_desc/use ]]; then
        echo "N0JCG USB gadget is already active."
        exit 0
    fi
    # Upgrade an older gadget definition in place so RNDIS OS descriptors
    # are applied on the next bind without requiring manual ConfigFS work.
    echo "" > UDC
fi

# Use the Raspberry Pi RNDIS identity covered by the Raspberry Pi Windows INF.
# This is a Windows compatibility workaround; the N0JCG product strings remain
# visible to the operator while the USB identity matches the installed driver.
echo 0x2e8a > idVendor
echo 0x0013 > idProduct
echo 0x0200 > bcdUSB
echo 0x0100 > bcdDevice
[[ -d strings/0x409 ]] || mkdir -p strings/0x409
echo N0JCG > strings/0x409/manufacturer
echo PI-WINLINK > strings/0x409/product
echo 0123456789 > strings/0x409/serialnumber
[[ -d configs/c.1/strings/0x409 ]] || mkdir -p configs/c.1/strings/0x409
echo "N0JCG Winlink USB network" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
USB_FUNCTION=""
if [[ -d functions/rndis.usb0 ]]; then
    USB_FUNCTION="rndis.usb0"
elif mkdir -p functions/rndis.usb0; then
    USB_FUNCTION="rndis.usb0"
elif [[ -d functions/ecm.usb0 ]]; then
    USB_FUNCTION="ecm.usb0"
elif mkdir -p functions/ecm.usb0; then
    USB_FUNCTION="ecm.usb0"
else
    echo "N0JCG USB gadget: this kernel provides neither RNDIS nor ECM." >&2
    exit 1
fi
if [[ ! -e "configs/c.1/$USB_FUNCTION" && ! -L "configs/c.1/$USB_FUNCTION" ]]; then
    ln -s "functions/$USB_FUNCTION" "configs/c.1/$USB_FUNCTION"
fi

# Advertise the Microsoft RNDIS signature so Windows initializes the
# network function instead of treating it as an ambiguous composite device.
# The RNDIS compatible-id group is created by usb_f_rndis under the function;
# this vendor kernel rejects a second interface.rndis group at gadget root.
if [[ "$USB_FUNCTION" == "rndis.usb0" ]]; then
    RNDIS_OS_DESC="functions/rndis.usb0/os_desc/interface.rndis"
    if [[ ! -d "$RNDIS_OS_DESC" ]]; then
        echo "N0JCG USB gadget: RNDIS OS descriptor group is unavailable." >&2
        exit 1
    fi
    [[ -d os_desc ]] || mkdir os_desc
    echo 0xcd > os_desc/b_vendor_code
    echo MSFT100 > os_desc/qw_sign
    echo RNDIS > "$RNDIS_OS_DESC/compatible_id"
    if [[ ! -e os_desc/c.1 && ! -L os_desc/c.1 ]]; then
        ln -s configs/c.1 os_desc/c.1
    fi
    # The configuration must be linked before OS descriptors are enabled.
    echo 1 > os_desc/use
fi
udevadm settle -t 5 2>/dev/null || true
echo "$UDC" > UDC

for _ in {1..20}; do
    [[ -d /sys/class/net/usb0 ]] && break
    sleep 0.25
done

echo "N0JCG USB Ethernet gadget active on $UDC."
