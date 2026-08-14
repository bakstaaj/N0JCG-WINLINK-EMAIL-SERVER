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
INSTALL_OPTIONS=()
CONNECTIVITY_ENV_FILE=""
REMOTE_CONNECTIVITY_ENV="$REMOTE_ROOT/connectivity.env"

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

for option in "${3:-}" "${4:-}"; do
    [[ -z "$option" ]] && continue
    case "$option" in
        --configure-operator-auth|--configure-connectivity) INSTALL_OPTIONS+=("$option") ;;
        *) echo "FAIL: unsupported installer option: $option" >&2; exit 1 ;;
    esac
done

cleanup_connectivity_env() {
    [[ -z "$CONNECTIVITY_ENV_FILE" ]] && return 0
    rm -f "$CONNECTIVITY_ENV_FILE"
}
trap cleanup_connectivity_env EXIT

if printf '%s\n' "${INSTALL_OPTIONS[@]}" | grep -qx -- '--configure-connectivity'; then
    printf 'Preferred Wi-Fi SSID (leave blank to skip preferred Wi-Fi): '
    read -r N0JCG_WIFI_SSID
    N0JCG_WIFI_PASSWORD=""
    if [[ -n "$N0JCG_WIFI_SSID" ]]; then
        printf 'Preferred Wi-Fi password: '
        read -r -s N0JCG_WIFI_PASSWORD
        echo
    fi
    printf 'Fallback hotspot SSID [N0JCG-WES]: '
    read -r N0JCG_AP_SSID
    N0JCG_AP_SSID="${N0JCG_AP_SSID:-N0JCG-WES}"
    printf 'Fallback hotspot password [Password] (change recommended): '
    read -r -s N0JCG_AP_PASSWORD
    echo
    N0JCG_AP_PASSWORD="${N0JCG_AP_PASSWORD:-Password}"
    printf 'Confirm fallback hotspot password: '
    read -r -s N0JCG_AP_PASSWORD_CONFIRM
    echo
    [[ "$N0JCG_AP_PASSWORD" == "$N0JCG_AP_PASSWORD_CONFIRM" ]] || { echo "FAIL: hotspot passwords did not match" >&2; exit 1; }
    [[ ${#N0JCG_AP_PASSWORD} -ge 8 ]] || { echo "FAIL: hotspot password must be at least 8 characters" >&2; exit 1; }
    CONNECTIVITY_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/n0jcg-connectivity.XXXXXX")"
    {
        printf 'N0JCG_WIFI_SSID=%q\n' "$N0JCG_WIFI_SSID"
        printf 'N0JCG_WIFI_PASSWORD=%q\n' "$N0JCG_WIFI_PASSWORD"
        printf 'N0JCG_AP_SSID=%q\n' "$N0JCG_AP_SSID"
        printf 'N0JCG_AP_PASSWORD=%q\n' "$N0JCG_AP_PASSWORD"
    } > "$CONNECTIVITY_ENV_FILE"
    chmod 600 "$CONNECTIVITY_ENV_FILE"
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

if [[ -n "$CONNECTIVITY_ENV_FILE" ]]; then
    sshpass -e scp -o StrictHostKeyChecking=accept-new "$CONNECTIVITY_ENV_FILE" "$REMOTE_HOST:$REMOTE_CONNECTIVITY_ENV"
fi

REMOTE_INSTALL_ARGS="${INSTALL_OPTIONS[*]:-}"
REMOTE_CONNECTIVITY_ENV_ARG=""
if [[ -n "$CONNECTIVITY_ENV_FILE" ]]; then
    REMOTE_CONNECTIVITY_ENV_ARG="N0JCG_CONNECTIVITY_ENV='$REMOTE_CONNECTIVITY_ENV'"
fi
printf '%s\n' "$N0JCG_PI_PASSWORD" | sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "sudo -S -p '' env $REMOTE_CONNECTIVITY_ENV_ARG bash '$REMOTE_ROOT/deploy/install_static_ui.sh' $REMOTE_INSTALL_ARGS"
if [[ -n "$CONNECTIVITY_ENV_FILE" ]]; then
    sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "rm -f '$REMOTE_CONNECTIVITY_ENV'"
fi
sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "grep -q PAT_TELNET_URL /opt/n0jcg-winlink/api/n0jcg_webmail.py && systemctl is-active --quiet n0jcg-webmail.service && echo 'PASS: deployed API and active service verified'"
unset N0JCG_PI_PASSWORD SSHPASS
