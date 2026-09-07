#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
    start)
        systemctl start gpsd.socket >/dev/null 2>&1 || true
        systemctl start gpsd.service >/dev/null 2>&1 || true
        ;;
    stop)
        systemctl stop gpsd.service gpsd.socket >/dev/null 2>&1 || true
        ;;
    pause)
        systemctl stop gpsd.service gpsd.socket >/dev/null 2>&1 || true
        ;;
    resume)
        systemctl start gpsd.socket >/dev/null 2>&1 || true
        systemctl restart gpsd.service >/dev/null 2>&1 || true
        ;;
    *)
        echo "usage: $0 start|stop|pause|resume" >&2
        exit 2
        ;;
esac
