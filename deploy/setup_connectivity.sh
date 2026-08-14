#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="/etc/n0jcg-winlink"
CONFIG_FILE="$CONFIG_DIR/network.conf"
WIFI_CONNECTION="n0jcg-wifi"
HOTSPOT_CONNECTION="n0jcg-hotspot"
USB_CONNECTION="n0jcg-usb-gadget"
AP_ADDRESS="192.168.50.1/24"
AP_DHCP_RANGE="192.168.50.100,192.168.50.200"
USB_ADDRESS="192.168.60.1/24"

[[ "${EUID:-$(id -u)}" == "0" ]] || { echo "FAIL: run as root (sudo $0)" >&2; exit 1; }
FROM_ENV=0
if [[ -n "${N0JCG_CONNECTIVITY_ENV:-}" && -f "$N0JCG_CONNECTIVITY_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$N0JCG_CONNECTIVITY_ENV"
    set +a
    FROM_ENV=1
fi

prompt_value() {
    local label="$1" current="$2" value
    if [[ -n "$current" ]]; then
        printf '%s [%s]: ' "$label" "$current"
    else
        printf '%s: ' "$label"
    fi
    read -r value
    printf '%s' "${value:-$current}"
}

prompt_secret() {
    local label="$1" value confirm
    printf '%s: ' "$label"
    read -r -s value
    echo
    printf 'Confirm %s: ' "$label"
    read -r -s confirm
    echo
    [[ "$value" == "$confirm" ]] || { echo "FAIL: values did not match" >&2; exit 1; }
    printf '%s' "$value"
}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y network-manager
systemctl enable --now NetworkManager.service
install -d -m 0755 "$CONFIG_DIR"

WIFI_SSID="${N0JCG_WIFI_SSID:-}"
WIFI_PASSWORD="${N0JCG_WIFI_PASSWORD:-}"
AP_SSID="${N0JCG_AP_SSID:-N0JCG-WES}"
AP_PASSWORD="${N0JCG_AP_PASSWORD:-Password}"

if [[ "${1:-}" != "--noninteractive" && "$FROM_ENV" != "1" ]]; then
    WIFI_SSID="$(prompt_value 'Preferred Wi-Fi SSID' "$WIFI_SSID")"
    if [[ -n "$WIFI_SSID" && -z "$WIFI_PASSWORD" ]]; then
        WIFI_PASSWORD="$(prompt_secret 'Preferred Wi-Fi password')"
    elif [[ -n "$WIFI_SSID" ]]; then
        printf 'Preferred Wi-Fi password (press Enter to keep the saved value): '
        read -r -s entered
        echo
        WIFI_PASSWORD="${entered:-$WIFI_PASSWORD}"
    fi
    AP_SSID="$(prompt_value 'Fallback hotspot SSID' "$AP_SSID")"
    printf 'Fallback hotspot password [%s] (change recommended): ' "$AP_PASSWORD"
    read -r -s entered
    echo
    AP_PASSWORD="${entered:-$AP_PASSWORD}"
    printf 'Confirm fallback hotspot password: '
    read -r -s confirm
    echo
    [[ "$AP_PASSWORD" == "$confirm" ]] || { echo "FAIL: values did not match" >&2; exit 1; }
fi

if [[ -n "$AP_PASSWORD" && ${#AP_PASSWORD} -lt 8 ]]; then
    echo "FAIL: fallback hotspot password must be at least 8 characters" >&2
    exit 1
fi

cat > "$CONFIG_FILE" <<EOF
N0JCG_WIFI_SSID=$(printf '%q' "$WIFI_SSID")
N0JCG_AP_SSID=$(printf '%q' "$AP_SSID")
N0JCG_AP_ADDRESS=$AP_ADDRESS
N0JCG_AP_DHCP_RANGE=$AP_DHCP_RANGE
N0JCG_USB_ADDRESS=$USB_ADDRESS
EOF
chmod 0600 "$CONFIG_FILE"

nmcli connection delete "$WIFI_CONNECTION" >/dev/null 2>&1 || true
nmcli connection delete "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
nmcli connection delete "$USB_CONNECTION" >/dev/null 2>&1 || true

if [[ -n "$WIFI_SSID" ]]; then
    nmcli connection add type wifi ifname wlan0 con-name "$WIFI_CONNECTION" ssid "$WIFI_SSID"
    nmcli connection modify "$WIFI_CONNECTION" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASSWORD" ipv4.method auto ipv6.method auto connection.autoconnect yes connection.autoconnect-priority 100
fi

nmcli connection add type wifi ifname wlan0 con-name "$HOTSPOT_CONNECTION" ssid "$AP_SSID"
nmcli connection modify "$HOTSPOT_CONNECTION" 802-11-wireless.mode ap 802-11-wireless.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$AP_PASSWORD" ipv4.method shared ipv4.addresses "$AP_ADDRESS" ipv4.shared-dhcp-range "$AP_DHCP_RANGE" ipv6.method disabled connection.autoconnect no

nmcli connection add type ethernet ifname usb0 con-name "$USB_CONNECTION"
nmcli connection modify "$USB_CONNECTION" ipv4.method shared ipv4.addresses "$USB_ADDRESS" ipv6.method disabled connection.autoconnect yes

install -m 0755 "$(dirname "$0")/n0jcg-usb-gadget.sh" /usr/local/sbin/n0jcg-usb-gadget.sh
install -m 0755 "$(dirname "$0")/n0jcg-network-fallback.sh" /usr/local/sbin/n0jcg-network-fallback.sh
sed 's/@APP_USER@/'"${N0JCG_APP_USER:-pi}"'/g' "$(dirname "$0")/n0jcg-usb-gadget.service" > /etc/systemd/system/n0jcg-usb-gadget.service
install -m 0644 "$(dirname "$0")/n0jcg-network-fallback.service" /etc/systemd/system/n0jcg-network-fallback.service

BOOT_CONFIG="/boot/firmware/config.txt"
[[ -f "$BOOT_CONFIG" ]] || BOOT_CONFIG="/boot/config.txt"
if [[ -f "$BOOT_CONFIG" ]] && ! grep -q '^dtoverlay=dwc2' "$BOOT_CONFIG"; then
    printf '\n# N0JCG USB Ethernet gadget support\ndtoverlay=dwc2\n' >> "$BOOT_CONFIG"
fi
CMDLINE="/boot/firmware/cmdline.txt"
[[ -f "$CMDLINE" ]] || CMDLINE="/boot/cmdline.txt"
if [[ -f "$CMDLINE" ]] && ! grep -q 'modules-load=.*dwc2' "$CMDLINE"; then
    sed -i 's/ rootwait/ modules-load=dwc2 rootwait/' "$CMDLINE"
fi

systemctl daemon-reload
systemctl enable n0jcg-usb-gadget.service n0jcg-network-fallback.service
systemctl restart n0jcg-usb-gadget.service || true
systemctl restart n0jcg-network-fallback.service
echo "PASS: Wi-Fi and fallback hotspot configured"
echo "INFO: preferred Wi-Fi uses DHCP; fallback hotspot is $AP_SSID at $AP_ADDRESS with leases $AP_DHCP_RANGE"
echo "INFO: USB gadget uses 192.168.60.1 on the Pi 4 USB-C power/data port"
echo "INFO: reboot required if dwc2 was newly added to the boot configuration"
