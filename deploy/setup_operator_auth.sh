#!/usr/bin/env bash
set -euo pipefail

AUTH_FILE="/etc/nginx/.htpasswd-n0jcg-winlink"
AUTH_SNIPPET="/etc/nginx/snippets/n0jcg-winlink-auth.conf.optional"
FROM_ENV=0

if [[ -n "${N0JCG_OPERATOR_AUTH_ENV:-}" && -f "$N0JCG_OPERATOR_AUTH_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$N0JCG_OPERATOR_AUTH_ENV"
    set +a
    FROM_ENV=1
fi

command -v htpasswd >/dev/null || {
    echo "FAIL: htpasswd is required; install apache2-utils first" >&2
    exit 1
}

if [[ "$FROM_ENV" != "1" ]]; then
    printf '%s' 'Operator username (required, for example operator): '
    read -r OPERATOR_USER
    if [[ -z "$OPERATOR_USER" ]]; then
        echo "FAIL: operator username cannot be empty" >&2
        exit 1
    fi
    printf '%s' "Password for operator '$OPERATOR_USER': "
    read -r -s OPERATOR_PASSWORD
    echo
    printf '%s' "Confirm password for operator '$OPERATOR_USER': "
    read -r -s OPERATOR_PASSWORD_CONFIRM
    echo
else
    OPERATOR_PASSWORD_CONFIRM="${OPERATOR_PASSWORD_CONFIRM:-$OPERATOR_PASSWORD}"
fi
if [[ -z "${OPERATOR_USER:-}" ]]; then
    echo "FAIL: operator username cannot be empty" >&2
    exit 1
fi
if [[ -z "$OPERATOR_PASSWORD" ]]; then
    echo "FAIL: operator password cannot be empty" >&2
    exit 1
fi
if [[ "$OPERATOR_PASSWORD" != "$OPERATOR_PASSWORD_CONFIRM" ]]; then
    echo "FAIL: passwords do not match; no operator account was changed" >&2
    exit 1
fi

printf '%s\n' "$OPERATOR_PASSWORD" | sudo htpasswd -iB -c "$AUTH_FILE" "$OPERATOR_USER"
sudo chmod 0640 "$AUTH_FILE"
sudo chown root:www-data "$AUTH_FILE"
sudo install -d -m 0755 /etc/nginx/snippets
sudo sh -c "cat > '$AUTH_SNIPPET' <<'EOF'
auth_basic \"N0JCG Winlink Email Server operator console\";
auth_basic_user_file /etc/nginx/.htpasswd-n0jcg-winlink;
EOF"
sudo nginx -t
sudo systemctl reload nginx
echo "PASS: operator authentication enabled for /ui/"
