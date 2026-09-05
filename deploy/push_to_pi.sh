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
CONNECTIVITY_REQUIRED=0
AUTH_ENV_FILE=""
REMOTE_AUTH_ENV="$REMOTE_ROOT/operator-auth.env"
AUTH_REQUIRED=0

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
    [[ -z "$CONNECTIVITY_ENV_FILE" ]] || rm -f "$CONNECTIVITY_ENV_FILE"
    [[ -z "$AUTH_ENV_FILE" ]] || rm -f "$AUTH_ENV_FILE"
}
trap cleanup_connectivity_env EXIT

if ! sshpass -e ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "test -f /etc/nginx/.htpasswd-n0jcg-winlink" >/dev/null 2>&1; then
    AUTH_REQUIRED=1
    echo "INFO: no operator authentication configuration found; initial operator setup is required."
fi

if printf '%s\n' "${INSTALL_OPTIONS[@]}" | grep -qx -- '--configure-operator-auth' || [[ "$AUTH_REQUIRED" == "1" ]]; then
    printf 'Operator username [operator]: '
    read -r N0JCG_OPERATOR_USER
    N0JCG_OPERATOR_USER="${N0JCG_OPERATOR_USER:-operator}"
    printf "Password for operator '%s': " "$N0JCG_OPERATOR_USER"
    read -r -s N0JCG_OPERATOR_PASSWORD
    echo
    printf "Confirm password for operator '%s': " "$N0JCG_OPERATOR_USER"
    read -r -s N0JCG_OPERATOR_PASSWORD_CONFIRM
    echo
    [[ -n "$N0JCG_OPERATOR_PASSWORD" ]] || { echo "FAIL: operator password cannot be empty" >&2; exit 1; }
    [[ "$N0JCG_OPERATOR_PASSWORD" == "$N0JCG_OPERATOR_PASSWORD_CONFIRM" ]] || { echo "FAIL: operator passwords did not match" >&2; exit 1; }
    AUTH_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/n0jcg-operator-auth.XXXXXX")"
    {
        printf 'OPERATOR_USER=%q\n' "$N0JCG_OPERATOR_USER"
        printf 'OPERATOR_PASSWORD=%q\n' "$N0JCG_OPERATOR_PASSWORD"
    } > "$AUTH_ENV_FILE"
    chmod 600 "$AUTH_ENV_FILE"
fi

if ! sshpass -e ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "test -f /etc/n0jcg-winlink/network.conf" >/dev/null 2>&1; then
    CONNECTIVITY_REQUIRED=1
    echo "INFO: no existing connectivity configuration found; initial hotspot setup is required."
fi

if printf '%s\n' "${INSTALL_OPTIONS[@]}" | grep -qx -- '--configure-connectivity' || [[ "$CONNECTIVITY_REQUIRED" == "1" ]]; then
    if ! printf '%s\n' "${INSTALL_OPTIONS[@]}" | grep -qx -- '--configure-connectivity'; then
        INSTALL_OPTIONS+=(--configure-connectivity)
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
if [[ -n "$AUTH_ENV_FILE" ]]; then
    sshpass -e scp -o StrictHostKeyChecking=accept-new "$AUTH_ENV_FILE" "$REMOTE_HOST:$REMOTE_AUTH_ENV"
fi

REMOTE_INSTALL_ARGS="${INSTALL_OPTIONS[*]:-}"
REMOTE_ENV_ARGS=""
if [[ -n "$CONNECTIVITY_ENV_FILE" ]]; then
    REMOTE_ENV_ARGS="N0JCG_CONNECTIVITY_ENV='$REMOTE_CONNECTIVITY_ENV'"
fi
if [[ -n "$AUTH_ENV_FILE" ]]; then
    REMOTE_ENV_ARGS="$REMOTE_ENV_ARGS N0JCG_OPERATOR_AUTH_ENV='$REMOTE_AUTH_ENV'"
fi
printf '%s\n' "$N0JCG_PI_PASSWORD" | sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "sudo -S -p '' env $REMOTE_ENV_ARGS bash '$REMOTE_ROOT/deploy/install_static_ui.sh' $REMOTE_INSTALL_ARGS"
if [[ -n "$CONNECTIVITY_ENV_FILE" ]]; then
    sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "rm -f '$REMOTE_CONNECTIVITY_ENV'"
fi
if [[ -n "$AUTH_ENV_FILE" ]]; then
    sshpass -e ssh -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "rm -f '$REMOTE_AUTH_ENV'"
fi
echo "INFO: waiting for the Pi to reboot and return online..."
verified=0
for attempt in {1..30}; do
    if sshpass -e ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new "$REMOTE_HOST" "grep -q PAT_TELNET_URL /opt/n0jcg-winlink/api/n0jcg_webmail.py && systemctl is-active --quiet n0jcg-webmail.service" >/dev/null 2>&1; then
        verified=1
        break
    fi
    sleep 2
done
if [[ "$verified" != "1" ]]; then
    echo "FAIL: Pi did not return with an active Webmail service after reboot" >&2
    exit 1
fi
echo "PASS: deployed API and active service verified after reboot"
unset N0JCG_PI_PASSWORD SSHPASS
