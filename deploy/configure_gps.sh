#!/usr/bin/env bash
set -euo pipefail

# Configure gpsd against the stable USB identity when one is available. This
# avoids binding the WES to a transient ttyACM/ttyUSB number after reboot.
GPS_DEVICE=""
for candidate in /dev/serial/by-id/*; do
    [[ -e "$candidate" ]] || continue
    case "$(basename "$candidate" | tr '[:upper:]' '[:lower:]')" in
        *gps*|*gnss*|*u-blox*) GPS_DEVICE="$candidate"; break ;;
    esac
done

sudo install -d -m 0755 /etc/default
if [[ -n "$GPS_DEVICE" ]]; then
    printf 'DEVICES="%s"\nGPSD_OPTIONS="-n"\n' "$GPS_DEVICE" | sudo tee /etc/default/gpsd >/dev/null
    echo "PASS: gpsd configured for $GPS_DEVICE"
else
    echo "INFO: no stable USB GPS identity found; gpsd will remain available for auto-detection"
fi
sudo systemctl enable --now gpsd.socket >/dev/null 2>&1 || true
sudo systemctl restart gpsd.service >/dev/null 2>&1 || true
