#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/var/www/n0jcg-winlink}"
APP_ROOT="${APP_ROOT:-/opt/n0jcg-winlink}"
APP_USER="${N0JCG_APP_USER:-${SUDO_USER:-pi}}"
NGINX_SITE="/etc/nginx/sites-available/n0jcg-winlink.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/n0jcg-winlink.conf"
CONFIGURE_OPERATOR_AUTH=0
CONFIGURE_CONNECTIVITY=0
INITIAL_CONNECTIVITY=0

for option in "$@"; do
    case "$option" in
        --configure-operator-auth) CONFIGURE_OPERATOR_AUTH=1 ;;
        --configure-connectivity) CONFIGURE_CONNECTIVITY=1 ;;
        --check-only) ;;
        *) echo "FAIL: unknown option: $option" >&2; exit 1 ;;
    esac
done

if [[ ! -f "$REPO_ROOT/ui/index.html" || ! -f "$REPO_ROOT/branding/tokens.css" ]]; then
    echo "FAIL: run this installer from the N0JCG-WINLINK-EMAIL-SERVER source tree" >&2
    echo "      expected: $REPO_ROOT/ui/index.html" >&2
    exit 1
fi

if [[ "${1:-}" == "--check-only" ]]; then
    test -f "$REPO_ROOT/ui/index.html"
    test -f "$REPO_ROOT/ui/styles.css"
    test -f "$REPO_ROOT/ui/webmail/index.html"
    test -f "$REPO_ROOT/ui/webmail/webmail.js"
    test -f "$REPO_ROOT/api/n0jcg_webmail.py"
    test -f "$REPO_ROOT/api/winlink_templates.py"
    test -f "$REPO_ROOT/deploy/setup_connectivity.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-usb-gadget.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-network-fallback.sh"
    test -f "$REPO_ROOT/branding/tokens.css"
    test -f "$REPO_ROOT/assets/brand/n0jcg-primary-light.svg"
    echo "PASS: static UI source and brand assets are present"
    exit 0
fi

if [[ ! -f /etc/n0jcg-winlink/network.conf ]]; then
    INITIAL_CONNECTIVITY=1
fi

sudo install -d -m 0755 "$INSTALL_ROOT/ui" "$INSTALL_ROOT/webmail" "$INSTALL_ROOT/branding" "$INSTALL_ROOT/assets/brand"
sudo install -d -m 0755 "$APP_ROOT/api" "$APP_ROOT/config" "$APP_ROOT/tools" /var/lib/n0jcg-winlink /var/lib/n0jcg-winlink-webmail
sudo install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.local/state/pat"
sudo install -m 0644 "$REPO_ROOT/ui/index.html" "$INSTALL_ROOT/ui/index.html"
sudo install -m 0644 "$REPO_ROOT/ui/styles.css" "$INSTALL_ROOT/ui/styles.css"
sudo install -m 0644 "$REPO_ROOT/ui/styles.css" "$INSTALL_ROOT/styles.css"
sudo install -m 0644 "$REPO_ROOT/ui/webmail/index.html" "$INSTALL_ROOT/webmail/index.html"
sudo install -m 0644 "$REPO_ROOT/ui/webmail/webmail.js" "$INSTALL_ROOT/webmail/webmail.js"
sudo install -m 0644 "$REPO_ROOT/ui/styles.css" "$INSTALL_ROOT/webmail/styles.css"
sudo install -m 0644 "$REPO_ROOT/branding/tokens.css" "$INSTALL_ROOT/branding/tokens.css"
sudo install -m 0644 "$REPO_ROOT/assets/brand/n0jcg-primary-light.svg" "$INSTALL_ROOT/assets/brand/n0jcg-primary-light.svg"
sudo install -m 0644 "$REPO_ROOT/assets/brand/N0JCG_Header_Dark_Approved.png" "$INSTALL_ROOT/assets/brand/N0JCG_Header_Dark_Approved.png"
sudo install -m 0644 "$REPO_ROOT/config/registration.example.json" "$APP_ROOT/config/registration.example.json"
sudo install -m 0755 "$REPO_ROOT/tools/registration.py" "$APP_ROOT/tools/registration.py"
sudo install -m 0755 "$REPO_ROOT/api/n0jcg_webmail.py" "$APP_ROOT/api/n0jcg_webmail.py"
sudo install -m 0644 "$REPO_ROOT/api/winlink_templates.py" "$APP_ROOT/api/winlink_templates.py"
sed "s/@APP_USER@/$APP_USER/g" "$REPO_ROOT/deploy/n0jcg-webmail.service" | sudo tee /etc/systemd/system/n0jcg-webmail.service >/dev/null
sudo chown -R "$APP_USER:$APP_USER" /var/lib/n0jcg-winlink-webmail
sudo install -m 0755 "$REPO_ROOT/deploy/setup_operator_auth.sh" "$APP_ROOT/tools/setup_operator_auth.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/setup_connectivity.sh" "$APP_ROOT/tools/setup_connectivity.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-gadget.sh" "$APP_ROOT/tools/n0jcg-usb-gadget.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-network-fallback.sh" "$APP_ROOT/tools/n0jcg-network-fallback.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/update_standard_forms.sh" "$APP_ROOT/tools/update_standard_forms.sh"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-usb-gadget.service" "$APP_ROOT/tools/n0jcg-usb-gadget.service"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-network-fallback.service" "$APP_ROOT/tools/n0jcg-network-fallback.service"
sudo install -m 0644 "$REPO_ROOT/deploy/nginx/n0jcg-winlink.conf" "$NGINX_SITE"
sudo rm -f /etc/nginx/sites-enabled/default
if [[ ! -f /etc/nginx/snippets/n0jcg-winlink-auth.conf.optional ]]; then
    printf 'auth_basic off;\n' | sudo tee /etc/nginx/snippets/n0jcg-winlink-auth.conf.optional >/dev/null
fi
sudo ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl daemon-reload
sudo systemctl enable n0jcg-webmail.service
sudo systemctl restart n0jcg-webmail.service
if ! grep -q 'PAT_TELNET_URL' "$APP_ROOT/api/n0jcg_webmail.py"; then
    echo "FAIL: installed webmail API is missing the current Pat authentication revision" >&2
    exit 1
fi
echo "PASS: webmail service restarted with current API revision"

if [[ "$CONFIGURE_CONNECTIVITY" == "1" || "$INITIAL_CONNECTIVITY" == "1" ]]; then
    if [[ -n "${N0JCG_CONNECTIVITY_ENV:-}" ]]; then
        sudo env N0JCG_CONNECTIVITY_ENV="$N0JCG_CONNECTIVITY_ENV" bash "$APP_ROOT/tools/setup_connectivity.sh"
    else
        sudo bash "$APP_ROOT/tools/setup_connectivity.sh"
    fi
else
    echo "INFO: Wi-Fi/USB gadget setup preserved; rerun with --configure-connectivity to reconfigure it."
fi

if [[ ! -f /etc/nginx/.htpasswd-n0jcg-winlink || "$CONFIGURE_OPERATOR_AUTH" == "1" ]]; then
    if ! command -v htpasswd >/dev/null 2>&1; then
        echo "Installing the password utility required for operator authentication."
        sudo apt-get update
        sudo apt-get install -y apache2-utils
    fi
    echo "Initial operator setup is required."
    bash "$APP_ROOT/tools/setup_operator_auth.sh"
else
    echo "PASS: existing operator authentication preserved"
fi

echo "PASS: N0JCG Winlink Email Server installed at http://$(hostname -I | awk '{print $1}')/webmail/"
echo "INFO: operator console is available at http://$(hostname -I | awk '{print $1}')/ui/"
