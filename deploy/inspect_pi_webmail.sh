#!/usr/bin/env bash
set -u

REMOTE_HOST="${1:-}"
if [[ -z "$REMOTE_HOST" ]]; then
    printf '%s' 'Pi IP address [192.168.68.149]: '
    read -r PI_IP
    PI_IP="${PI_IP:-192.168.68.149}"
    printf '%s' 'Pi SSH username [pi]: '
    read -r PI_USER
    PI_USER="${PI_USER:-pi}"
    REMOTE_HOST="$PI_USER@$PI_IP"
fi

ssh -tt "$REMOTE_HOST" 'echo ====ACTIVE NGINX API BLOCK====; sudo nginx -T 2>/dev/null | grep -A10 -B2 "location /api/v1" || true; echo ====SERVICE====; sudo systemctl status n0jcg-webmail.service --no-pager -l || true; echo ====PORT====; ss -ltnp | grep 8097 || true; echo ====INSTALLED====; sudo ls -l /opt/n0jcg-winlink/api/n0jcg_webmail.py /etc/nginx/sites-enabled/n0jcg-winlink.conf 2>&1 || true; exit'
status=$?
echo
echo "SSH diagnostic exited with status $status. Press Enter to close."
read -r
exit "$status"
