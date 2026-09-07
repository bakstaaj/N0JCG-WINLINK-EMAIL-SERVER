#!/usr/bin/env bash
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/n0jcg
UDC=""
for _ in {1..30}; do
    for candidate in /sys/class/udc/*xhci2-controller; do
        if [[ -e "$candidate" ]]; then
            UDC="$(basename "$candidate")"
            break 2
        fi
    done
    sleep 1
done

# On the Orange Pi the other UDC is a host-side controller and must never be
# selected for gadget mode. On boards with exactly one UDC, retain the generic
# fallback used by the Raspberry Pi deployment.
if [[ -z "$UDC" ]]; then
    mapfile -t AVAILABLE_UDCS < <(find /sys/class/udc -mindepth 1 -maxdepth 1 -type l -printf '%f\n' 2>/dev/null)
    if [[ "${#AVAILABLE_UDCS[@]}" -eq 1 ]]; then
        UDC="${AVAILABLE_UDCS[0]}"
    fi
fi

if [[ -z "$UDC" ]]; then
    echo "N0JCG USB gadget: no USB device controller is available; use the Pi 4 USB-C power/data port." >&2
    exit 1
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
# Match the device-class descriptor used by Linux's legacy g_ether RNDIS
# configuration, which is the Windows-compatible reference implementation.
echo 0x02 > bDeviceClass
echo 0x00 > bDeviceSubClass
echo 0x00 > bDeviceProtocol
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
# Keep the RNDIS transmit queue conservative for Windows hosts. The vendor
# kernel defaults qmult to 5, which can make this controller repeatedly reset
# the host-side RNDIS interface under DHCP traffic.
if [[ "$USB_FUNCTION" == "rndis.usb0" ]]; then
    # ConfigFS otherwise randomizes both Ethernet endpoints each time the
    # RNDIS function is recreated. Windows treats the changing peer address
    # as a new/unstable adapter and can discard its DHCP state. Keep the
    # device address tied to the appliance Wi-Fi identity and use a stable
    # locally-administered peer address for the directly attached host.
    DEVICE_MAC="$(cat /sys/class/net/wlan0/address 2>/dev/null || true)"
    if [[ "$DEVICE_MAC" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]]; then
        echo "$DEVICE_MAC" > functions/rndis.usb0/dev_addr
    fi
    echo 02:00:00:60:00:02 > functions/rndis.usb0/host_addr
    echo 1 > functions/rndis.usb0/qmult
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
