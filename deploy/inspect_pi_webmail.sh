#!/usr/bin/env bash
set -u

command -v sshpass >/dev/null 2>&1 || {
    echo "FAIL: sshpass is required in MSYS2; install the sshpass package." >&2
    exit 1
}

PI_IP="${1:-}"
PI_USER="${2:-}"
if [[ -z "$PI_IP" ]]; then
    printf '%s' 'Pi IP address [192.168.68.149]: '
    read -r PI_IP
    PI_IP="${PI_IP:-192.168.68.149}"
fi
if [[ -z "$PI_USER" ]]; then
    printf '%s' 'Pi SSH username [pi]: '
    read -r PI_USER
    PI_USER="${PI_USER:-pi}"
fi
REMOTE_HOST="$PI_USER@$PI_IP"

if [[ -z "${N0JCG_PI_PASSWORD:-}" ]]; then
    printf 'Pi SSH password: '
    read -r -s N0JCG_PI_PASSWORD
    echo
fi
export SSHPASS="$N0JCG_PI_PASSWORD"
unset N0JCG_PI_PASSWORD

sshpass -e ssh -tt -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" 'echo ====ACTIVE NGINX API BLOCK====; sudo nginx -T 2>/dev/null | grep -A10 -B2 "location /api/v1" || true; echo ====SERVICE====; sudo systemctl status n0jcg-webmail.service --no-pager -l || true; echo ====PORT====; ss -ltnp | grep 8097 || true; echo ====INSTALLED====; sudo ls -l /opt/n0jcg-winlink/api/n0jcg_webmail.py /etc/nginx/sites-enabled/n0jcg-winlink.conf 2>&1 || true; exit'
status=$?
echo
echo "SSH diagnostic exited with status $status. Press Enter to close."
read -r
exit "$status"
