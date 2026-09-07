#!/usr/bin/env bash
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/n0jcg
[[ -d "$GADGET" ]] || exit 0
if [[ -f "$GADGET/UDC" ]]; then
    printf '' > "$GADGET/UDC" || true
fi
rm -f "$GADGET/os_desc/c.1" "$GADGET/configs/c.1/rndis.usb0" "$GADGET/configs/c.1/ecm.usb0"
rmdir "$GADGET/functions/rndis.usb0/os_desc/interface.rndis" 2>/dev/null || true
rmdir "$GADGET/functions/rndis.usb0/os_desc" 2>/dev/null || true
rmdir "$GADGET/functions/rndis.usb0" 2>/dev/null || true
rmdir "$GADGET/functions/ecm.usb0" 2>/dev/null || true
rmdir "$GADGET/configs/c.1/strings/0x409" 2>/dev/null || true
rmdir "$GADGET/configs/c.1" 2>/dev/null || true
rmdir "$GADGET/strings/0x409" 2>/dev/null || true
rmdir "$GADGET/strings" 2>/dev/null || true
rmdir "$GADGET/configs" 2>/dev/null || true
rmdir "$GADGET/functions" 2>/dev/null || true
rmdir "$GADGET/os_desc" 2>/dev/null || true
rmdir "$GADGET" 2>/dev/null || true
