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
    test -f "$REPO_ROOT/api/rms_gateways.py"
    test -f "$REPO_ROOT/api/winlink_templates.py"
    test -f "$REPO_ROOT/deploy/setup_connectivity.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-usb-gadget.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-usb-gadget-remove.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-usb-network.sh"
    test -f "$REPO_ROOT/deploy/n0jcg-usb-network.service"
    test -f "$REPO_ROOT/deploy/n0jcg-network-fallback.sh"
    test -f "$REPO_ROOT/deploy/refresh_install_caches.sh"
    test -f "$REPO_ROOT/deploy/apply_operator_settings.py"
    test -f "$REPO_ROOT/deploy/configure_packet_radio.sh"
    test -f "$REPO_ROOT/deploy/setup_digirig.sh"
    test -f "$REPO_ROOT/deploy/configure_gps.sh"
    test -f "$REPO_ROOT/tools/agwpe_identity_bridge.py"
    test -f "$REPO_ROOT/deploy/n0jcg-agwpe-identity-bridge.service"
    test -f "$REPO_ROOT/branding/tokens.css"
    test -f "$REPO_ROOT/assets/brand/n0jcg-primary-light.svg"
    echo "PASS: static UI source and brand assets are present"
    exit 0
fi

if ! command -v nginx >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "FAIL: Nginx is not installed and this system has no supported apt package manager" >&2
        exit 1
    fi
    echo "Nginx is not installed; installing it for the Webmail service."
    sudo apt-get update
    sudo apt-get install -y nginx dnsmasq iptables
fi
if command -v apt-get >/dev/null 2>&1; then
    missing_packages=()
    [[ -x /usr/sbin/dnsmasq || -x /sbin/dnsmasq ]] || missing_packages+=(dnsmasq)
    [[ -x /usr/sbin/iptables || -x /sbin/iptables ]] || missing_packages+=(iptables)
    if ((${#missing_packages[@]})); then
        sudo apt-get update
        sudo apt-get install -y "${missing_packages[@]}"
    fi
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "FAIL: systemd is required to run the WES services" >&2
    exit 1
fi

# Do not continue with a partially writable installation. Linux can remount
# the root filesystem read-only after storage or filesystem errors; catch that
# before writing application files and give the operator useful evidence.
if command -v findmnt >/dev/null 2>&1; then
    ROOT_MOUNT_OPTIONS="$(findmnt -T / -no OPTIONS 2>/dev/null || true)"
    if [[ ",${ROOT_MOUNT_OPTIONS}," != *,rw,* ]]; then
        echo "WARN: root filesystem is mounted read-only; attempting a remount."
        sudo mount -o remount,rw / >/dev/null 2>&1 || true
        ROOT_MOUNT_OPTIONS="$(findmnt -T / -no OPTIONS 2>/dev/null || true)"
    fi
    if [[ ",${ROOT_MOUNT_OPTIONS}," != *,rw,* ]]; then
        echo "FAIL: root filesystem remains read-only; installation cannot continue." >&2
        echo "INFO: inspect storage/filesystem errors with: findmnt -T /; dmesg | tail -80" >&2
        exit 30
    fi
    WRITE_TEST="/etc/.n0jcg-wes-write-test.$$"
    if ! sudo sh -c "umask 077; touch '$WRITE_TEST'" >/dev/null 2>&1; then
        echo "FAIL: /etc is not writable even though the root mount reports rw." >&2
        echo "INFO: inspect the filesystem and storage with: findmnt -T /etc; dmesg | tail -80" >&2
        exit 30
    fi
    sudo rm -f "$WRITE_TEST"
fi

if [[ ! -f /etc/n0jcg-winlink/network.conf ]]; then
    INITIAL_CONNECTIVITY=1
fi

sudo install -d -m 0755 "$INSTALL_ROOT/ui" "$INSTALL_ROOT/webmail" "$INSTALL_ROOT/branding" "$INSTALL_ROOT/assets/brand"
sudo install -d -m 0755 "$APP_ROOT/api" "$APP_ROOT/config" "$APP_ROOT/tools" /var/lib/n0jcg-winlink /var/lib/n0jcg-winlink-webmail
sudo install -d -m 0755 /etc/NetworkManager/dnsmasq-shared.d
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-hotspot-dnsmasq.conf" /etc/NetworkManager/dnsmasq-shared.d/n0jcg-hotspot.conf
# Ubuntu-based images, including Orange Pi images, may not create Debian's
# optional Nginx site directories. Create them before installing the site and
# snippet configuration so first deployment works on either layout.
sudo install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/snippets
sudo systemctl enable --now nginx
sudo chown "$APP_USER:$APP_USER" /var/lib/n0jcg-winlink
sudo install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.local/state/pat" "/home/$APP_USER/.config/pat" "/home/$APP_USER/.local/share/pat"
sudo install -m 0644 "$REPO_ROOT/ui/index.html" "$INSTALL_ROOT/ui/index.html"
sudo install -m 0644 "$REPO_ROOT/VERSION" "$INSTALL_ROOT/VERSION"
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
sudo install -d -m 0755 "$APP_ROOT/tools/n0jcg_licensing"
sudo install -m 0644 "$REPO_ROOT/tools/n0jcg_licensing/__init__.py" "$APP_ROOT/tools/n0jcg_licensing/__init__.py"
sudo install -m 0644 "$REPO_ROOT/tools/n0jcg_licensing/client.py" "$APP_ROOT/tools/n0jcg_licensing/client.py"
sudo install -m 0755 "$REPO_ROOT/api/n0jcg_webmail.py" "$APP_ROOT/api/n0jcg_webmail.py"
sudo install -m 0644 "$REPO_ROOT/api/winlink_templates.py" "$APP_ROOT/api/winlink_templates.py"
sudo install -m 0644 "$REPO_ROOT/api/rms_gateways.py" "$APP_ROOT/api/rms_gateways.py"
# MSYS2 may transfer checked-out Python helpers with CRLF endings. Linux
# shebangs must be LF-only or env will look for names such as python3\r.
for helper in "$APP_ROOT"/tools/*.py "$APP_ROOT"/tools/*.sh; do
    [[ -f "$helper" ]] && sudo sed -i 's/\r$//' "$helper"
done
sed "s/@APP_USER@/$APP_USER/g" "$REPO_ROOT/deploy/n0jcg-webmail.service" | sudo tee /etc/systemd/system/n0jcg-webmail.service >/dev/null
sudo chown -R "$APP_USER:$APP_USER" /var/lib/n0jcg-winlink-webmail

# The gateway finder reads standard USB GPS fixes through gpsd. Install the
# client tools during initial deployment; no GPS device is required for the
# simulated-location mode.
if ! command -v gpspipe >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y gpsd gpsd-clients
fi
sudo install -m 0755 "$REPO_ROOT/deploy/configure_gps.sh" "$APP_ROOT/tools/configure_gps.sh"
sudo "$APP_ROOT/tools/configure_gps.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/setup_operator_auth.sh" "$APP_ROOT/tools/setup_operator_auth.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/setup_connectivity.sh" "$APP_ROOT/tools/setup_connectivity.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-gadget.sh" "$APP_ROOT/tools/n0jcg-usb-gadget.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-gadget-remove.sh" "$APP_ROOT/tools/n0jcg-usb-gadget-remove.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-network.sh" "$APP_ROOT/tools/n0jcg-usb-network.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-network-fallback.sh" "$APP_ROOT/tools/n0jcg-network-fallback.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-gadget.sh" /usr/local/sbin/n0jcg-usb-gadget.sh
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-gadget-remove.sh" /usr/local/sbin/n0jcg-usb-gadget-remove.sh
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-usb-network.sh" /usr/local/sbin/n0jcg-usb-network.sh
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-network-fallback.sh" /usr/local/sbin/n0jcg-network-fallback.sh
sudo install -m 0755 "$REPO_ROOT/deploy/apply_operator_settings.py" "$APP_ROOT/tools/apply_operator_settings.py"
sudo install -m 0755 "$REPO_ROOT/deploy/update_standard_forms.sh" "$APP_ROOT/tools/update_standard_forms.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/refresh_install_caches.sh" "$APP_ROOT/tools/refresh_install_caches.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/configure_packet_radio.sh" "$APP_ROOT/tools/configure_packet_radio.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/setup_digirig.sh" "$APP_ROOT/tools/setup_digirig.sh"
sudo install -m 0755 "$REPO_ROOT/deploy/n0jcg-gps-rf-guard.sh" /usr/local/sbin/n0jcg-gps-rf-guard
sudo install -m 0755 "$REPO_ROOT/deploy/apply_radio_profile.sh" "$APP_ROOT/tools/apply_radio_profile.sh"
sudo install -m 0755 "$REPO_ROOT/tools/wes_direwolf_autogain.py" /usr/local/sbin/n0jcg-wes-direwolf-autogain
sudo install -m 0755 "$REPO_ROOT/tools/wes_audio_tee.py" /usr/local/sbin/n0jcg-wes-audio-tee
sudo install -m 0755 "$REPO_ROOT/tools/wes_apply_gain.py" /usr/local/sbin/n0jcg-wes-apply-gain
sudo install -m 0755 "$REPO_ROOT/tools/wes_audio_device.py" /usr/local/sbin/n0jcg-wes-audio-device
sudo install -m 0755 "$REPO_ROOT/tools/agwpe_identity_bridge.py" "$APP_ROOT/tools/agwpe_identity_bridge.py"
# Normalize the service helpers after their final installation location is set.
for helper in /usr/local/sbin/n0jcg-network-fallback.sh /usr/local/sbin/n0jcg-usb-gadget.sh /usr/local/sbin/n0jcg-wes-* "$APP_ROOT"/tools/*.py "$APP_ROOT"/tools/*.sh; do
    [[ -f "$helper" ]] && sudo sed -i 's/\r$//' "$helper"
done
sudo bash "$APP_ROOT/tools/setup_digirig.sh"

# Install the Pat ARM64 client. The bundled client-side build is based on the
# official Pat 0.17.0 source and uses the standard Winlink FBB exchange; its
# only protocol-role change is selecting client-initiated FBB for AX.25 Packet
# RMS. If the build artifact is absent, fall back to the official release.
PAT_VERSION="${N0JCG_PAT_VERSION:-0.17.0}"
PAT_ARCHIVE="pat_${PAT_VERSION}_linux_arm64.tar.gz"
PAT_URL="https://github.com/la5nta/pat/releases/download/v${PAT_VERSION}/${PAT_ARCHIVE}"
PAT_TMP="$(mktemp -d)"
trap 'rm -rf "$PAT_TMP"' EXIT
PAT_CLIENT_BINARY="$REPO_ROOT/tools/pat-winlink-client-rms"
if [[ -f "$PAT_CLIENT_BINARY" ]]; then
    sudo install -m 0755 "$PAT_CLIENT_BINARY" /usr/local/bin/pat
    echo "PASS: client-side Packet RMS Pat installed"
elif [[ ! -x /usr/local/bin/pat ]] || ! /usr/local/bin/pat version 2>/dev/null | grep -q "Pat v${PAT_VERSION}"; then
    curl --fail --location --silent --show-error "$PAT_URL" -o "$PAT_TMP/$PAT_ARCHIVE"
    tar -xzf "$PAT_TMP/$PAT_ARCHIVE" -C "$PAT_TMP"
    PAT_BINARY="$(find "$PAT_TMP" -type f -name pat -print -quit)"
    [[ -n "$PAT_BINARY" ]] || { echo "FAIL: official Pat binary was not found in $PAT_ARCHIVE" >&2; exit 1; }
    sudo install -m 0755 "$PAT_BINARY" /usr/local/bin/pat
fi
sudo /usr/local/bin/pat version
if ! command -v direwolf >/dev/null 2>&1; then
    echo "Dire Wolf is not installed; installing the packet modem."
    sudo apt-get update
    sudo apt-get install -y direwolf
fi
DIREWOLF_BIN="$(command -v direwolf)"
[[ -x "$DIREWOLF_BIN" ]] || { echo "FAIL: Dire Wolf binary is not executable" >&2; exit 1; }
echo "PASS: Dire Wolf available at $DIREWOLF_BIN"
sudo install -m 0644 "$REPO_ROOT/config/direwolf-n0jcg.conf.example" "$APP_ROOT/config/direwolf-n0jcg.conf.example"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-usb-gadget.service" "$APP_ROOT/tools/n0jcg-usb-gadget.service"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-usb-network.service" "$APP_ROOT/tools/n0jcg-usb-network.service"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-usb-gadget.service" /etc/systemd/system/n0jcg-usb-gadget.service
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-usb-network.service" /etc/systemd/system/n0jcg-usb-network.service
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-network-fallback.service" "$APP_ROOT/tools/n0jcg-network-fallback.service"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-direwolf.service" "$APP_ROOT/tools/n0jcg-direwolf.service"
sudo install -m 0644 "$REPO_ROOT/deploy/n0jcg-agwpe-identity-bridge.service" "$APP_ROOT/tools/n0jcg-agwpe-identity-bridge.service"
sed "s/@APP_USER@/$APP_USER/g" "$REPO_ROOT/deploy/n0jcg-agwpe-identity-bridge.service" | sudo tee /etc/systemd/system/n0jcg-agwpe-identity-bridge.service >/dev/null
sed -e "s/@APP_USER@/$APP_USER/g" -e "s#@DIREWOLF_BIN@#$DIREWOLF_BIN#g" "$REPO_ROOT/deploy/n0jcg-direwolf.service" | sudo tee /etc/systemd/system/n0jcg-direwolf.service >/dev/null
printf '%s ALL=(root) NOPASSWD: %s/tools/apply_radio_profile.sh\n' "$APP_USER" "$APP_ROOT" | sudo tee /etc/sudoers.d/n0jcg-radio-profile >/dev/null
printf '%s ALL=(root) NOPASSWD: %s/tools/apply_operator_settings.py\n' "$APP_USER" "$APP_ROOT" | sudo tee -a /etc/sudoers.d/n0jcg-radio-profile >/dev/null
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/n0jcg-gps-rf-guard\n' "$APP_USER" | sudo tee -a /etc/sudoers.d/n0jcg-radio-profile >/dev/null
sudo chmod 0440 /etc/sudoers.d/n0jcg-radio-profile
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
sudo env N0JCG_APP_ROOT="$APP_ROOT" N0JCG_APP_USER="$APP_USER" bash "$APP_ROOT/tools/refresh_install_caches.sh"
sudo systemctl daemon-reload
sudo systemctl enable n0jcg-agwpe-identity-bridge.service
sudo systemctl enable n0jcg-usb-gadget.service n0jcg-usb-network.service
sudo systemctl restart n0jcg-agwpe-identity-bridge.service
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
    if [[ -n "${N0JCG_OPERATOR_AUTH_ENV:-}" ]]; then
        env N0JCG_OPERATOR_AUTH_ENV="$N0JCG_OPERATOR_AUTH_ENV" bash "$APP_ROOT/tools/setup_operator_auth.sh"
    else
        bash "$APP_ROOT/tools/setup_operator_auth.sh"
    fi
else
    echo "PASS: existing operator authentication preserved"
fi

# Backfill metadata on upgrades from releases that predated operator.conf.
# Store only the username; never copy the password hash into this file.
if [[ -f /etc/nginx/.htpasswd-n0jcg-winlink && ! -f /etc/n0jcg-winlink/operator.conf ]]; then
    EXISTING_OPERATOR_USER="$(sudo awk -F: 'NR == 1 { print $1; exit }' /etc/nginx/.htpasswd-n0jcg-winlink)"
    if [[ -n "$EXISTING_OPERATOR_USER" ]]; then
        printf 'N0JCG_OPERATOR_USER=%q\n' "$EXISTING_OPERATOR_USER" | sudo tee /etc/n0jcg-winlink/operator.conf >/dev/null
        sudo chmod 0644 /etc/n0jcg-winlink/operator.conf
    fi
fi

echo "PASS: N0JCG Winlink Email Server installed at http://$(hostname -I | awk '{print $1}')/webmail/"
echo "INFO: operator console is available at http://$(hostname -I | awk '{print $1}')/ui/"
echo "INFO: installation complete; scheduling a system reboot."
sudo systemd-run --quiet --unit="n0jcg-wes-install-reboot-$(date +%s)" --on-active=5s /usr/bin/systemctl reboot
