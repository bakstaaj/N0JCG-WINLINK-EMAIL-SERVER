#!/usr/bin/env bash
set -euo pipefail

command -v sshpass >/dev/null 2>&1 || {
    echo "FAIL: sshpass is required in MSYS2; install the sshpass package." >&2
    exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_IP="${1:-}"
PI_USER="${2:-}"
REMOTE_ROOT="/tmp/n0jcg-winlink-source"
AUTH_OPTION="${3:-}"

if [[ -z "$PI_IP" ]]; then
    printf '%s' 'Raspberry Pi IP address [192.168.68.149]: '
    read -r PI_IP
    PI_IP="${PI_IP:-192.168.68.149}"
fi
if [[ -z "$PI_USER" ]]; then
    printf '%s' 'Raspberry Pi SSH username [pi]: '
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

if [[ -n "$AUTH_OPTION" && "$AUTH_OPTION" != "--configure-operator-auth" ]]; then
    echo "FAIL: supported optional flag is --configure-operator-auth" >&2
    exit 1
fi

for required in ui branding assets config deploy tools api; do
    test -d "$REPO_ROOT/$required" || {
        echo "FAIL: missing source directory: $required" >&2
        exit 1
    }
done

sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "mkdir -p '$REMOTE_ROOT'"
sshpass -e scp -o StrictHostKeyChecking=accept-new -r \
    "$REPO_ROOT/ui" "$REPO_ROOT/branding" "$REPO_ROOT/assets" \
    "$REPO_ROOT/config" "$REPO_ROOT/deploy" "$REPO_ROOT/tools" \
    "$REPO_ROOT/api" "$REMOTE_HOST:$REMOTE_ROOT/"

printf '%s\n' "$N0JCG_PI_PASSWORD" | sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "sudo -S -p '' bash '$REMOTE_ROOT/deploy/install_static_ui.sh' $AUTH_OPTION"
sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "grep -q PAT_TELNET_URL /opt/n0jcg-winlink/api/n0jcg_webmail.py && systemctl is-active --quiet n0jcg-webmail.service && echo 'PASS: deployed API and active service verified'"
unset N0JCG_PI_PASSWORD SSHPASS
