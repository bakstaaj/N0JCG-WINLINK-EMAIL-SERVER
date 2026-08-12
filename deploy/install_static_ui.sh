#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/var/www/n0jcg-winlink}"
APP_ROOT="${APP_ROOT:-/opt/n0jcg-winlink}"
NGINX_SITE="/etc/nginx/sites-available/n0jcg-winlink.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/n0jcg-winlink.conf"

if [[ ! -f "$REPO_ROOT/ui/index.html" || ! -f "$REPO_ROOT/branding/tokens.css" ]]; then
    echo "FAIL: run this installer from the N0JCG-WINLINK-EMAIL-SERVER source tree" >&2
    echo "      expected: $REPO_ROOT/ui/index.html" >&2
    exit 1
fi

if [[ "${1:-}" == "--check-only" ]]; then
    test -f "$REPO_ROOT/ui/index.html"
    test -f "$REPO_ROOT/ui/styles.css"
    test -f "$REPO_ROOT/branding/tokens.css"
    test -f "$REPO_ROOT/assets/brand/n0jcg-primary-light.svg"
    echo "PASS: static UI source and brand assets are present"
    exit 0
fi

sudo install -d -m 0755 "$INSTALL_ROOT/ui" "$INSTALL_ROOT/branding" "$INSTALL_ROOT/assets/brand"
sudo install -d -m 0755 "$APP_ROOT/config" "$APP_ROOT/tools" /var/lib/n0jcg-winlink
sudo install -m 0644 "$REPO_ROOT/ui/index.html" "$INSTALL_ROOT/ui/index.html"
sudo install -m 0644 "$REPO_ROOT/ui/styles.css" "$INSTALL_ROOT/ui/styles.css"
sudo install -m 0644 "$REPO_ROOT/branding/tokens.css" "$INSTALL_ROOT/branding/tokens.css"
sudo install -m 0644 "$REPO_ROOT/assets/brand/n0jcg-primary-light.svg" "$INSTALL_ROOT/assets/brand/n0jcg-primary-light.svg"
sudo install -m 0644 "$REPO_ROOT/assets/brand/N0JCG_Header_Dark_Approved.png" "$INSTALL_ROOT/assets/brand/N0JCG_Header_Dark_Approved.png"
sudo install -m 0644 "$REPO_ROOT/config/registration.example.json" "$APP_ROOT/config/registration.example.json"
sudo install -m 0755 "$REPO_ROOT/tools/registration.py" "$APP_ROOT/tools/registration.py"
sudo install -m 0755 "$REPO_ROOT/deploy/setup_operator_auth.sh" "$APP_ROOT/tools/setup_operator_auth.sh"
sudo install -m 0644 "$REPO_ROOT/deploy/nginx/n0jcg-winlink.conf" "$NGINX_SITE"
if [[ ! -f /etc/nginx/snippets/n0jcg-winlink-auth.conf.optional ]]; then
    printf 'auth_basic off;\n' | sudo tee /etc/nginx/snippets/n0jcg-winlink-auth.conf.optional >/dev/null
fi
sudo ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
sudo nginx -t
sudo systemctl reload nginx

if [[ ! -f /etc/nginx/.htpasswd-n0jcg-winlink ]]; then
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

echo "PASS: N0JCG Winlink Email Server UI installed at http://$(hostname -I | awk '{print $1}'):8096/ui/"
