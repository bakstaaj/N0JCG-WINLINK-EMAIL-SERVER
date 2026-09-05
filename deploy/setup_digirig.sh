#!/usr/bin/env bash
set -euo pipefail

# DigiRig Mobile exposes USB audio plus a USB serial interface.  The serial
# adapter may enumerate as ttyUSB0, ttyUSB1, etc., so use stable udev aliases
# rather than a transient device number.  These are the USB serial chipsets
# used by DigiRig and by the compatible cables supported by the appliance.
[[ "${EUID:-$(id -u)}" == "0" ]] || { echo "FAIL: run with sudo" >&2; exit 1; }

RULE_FILE="/etc/udev/rules.d/70-n0jcg-digirig.rules"
install -d -m 0755 /etc/udev/rules.d
cat > "$RULE_FILE" <<'EOF'
# N0JCG DigiRig/packet-radio serial and PTT aliases.
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="digirig-serial", SYMLINK+="digirig-ptt", MODE="0660", GROUP="dialout"
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="digirig-serial", SYMLINK+="digirig-ptt", MODE="0660", GROUP="dialout"
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="digirig-serial", SYMLINK+="digirig-ptt", MODE="0660", GROUP="dialout"
EOF
chmod 0644 "$RULE_FILE"
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=tty
udevadm settle --timeout=5 || true

if [[ -e /dev/digirig-serial ]]; then
    echo "PASS: DigiRig serial/PTT alias available at /dev/digirig-serial"
else
    echo "INFO: DigiRig audio may be connected, but no supported USB serial/PTT adapter is present"
fi
