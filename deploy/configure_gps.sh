#!/usr/bin/env bash
set -euo pipefail

# Configure gpsd against a stable udev alias. Never pass a shell wildcard to
# gpsd: gpsd treats DEVICES literally and will not expand it.
GPS_DEVICE=""
GPS_RULE='/etc/udev/rules.d/72-n0jcg-gps.rules'
printf 'KERNEL=="ttyACM*", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a7", SYMLINK+="n0jcg-gps"\n' | sudo tee "$GPS_RULE" >/dev/null
sudo chmod 0644 "$GPS_RULE"
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=tty
if [[ -e /dev/n0jcg-gps ]]; then
    GPS_DEVICE="/dev/n0jcg-gps"
fi
for candidate in /dev/serial/by-id/*; do
    [[ -e "$candidate" ]] || continue
    [[ -z "$GPS_DEVICE" ]] || break
    case "$(basename "$candidate" | tr '[:upper:]' '[:lower:]')" in
        *gps*|*gnss*|*u-blox*) GPS_DEVICE="$(readlink -f "$candidate")"; break ;;
    esac
done

sudo install -d -m 0755 /etc/default
if [[ -n "$GPS_DEVICE" ]]; then
    printf 'DEVICES="%s"\nGPSD_OPTIONS="-n"\n' "$GPS_DEVICE" | sudo tee /etc/default/gpsd >/dev/null
    echo "PASS: gpsd configured for $GPS_DEVICE"
else
    echo "INFO: no stable USB GPS identity found; gpsd will remain available for auto-detection"
fi
# GPS is intentionally on demand. WES starts it only while the operator asks
# for a GPS fix, then stops it again before RF work can use the USB bus.
sudo systemctl disable --now gpsd.socket gpsd.service >/dev/null 2>&1 || true
echo "PASS: gpsd configured for on-demand polling"
