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
NETPLAN_OVERRIDE="/etc/netplan/99-n0jcg-wes-networkmanager.yaml"

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

detect_network_backend() {
    local renderer=""
    if command -v netplan >/dev/null 2>&1 && compgen -G '/etc/netplan/*.yaml' >/dev/null; then
        renderer="$(netplan get network.renderer 2>/dev/null | tr -d '\"[:space:]' || true)"
        case "$renderer" in
            networkd) printf '%s' "netplan-networkd"; return 0 ;;
            NetworkManager|networkmanager) printf '%s' "netplan-networkmanager"; return 0 ;;
        esac
    fi
    if systemctl is-active --quiet NetworkManager.service 2>/dev/null; then
        printf '%s' "networkmanager"
    elif systemctl is-active --quiet systemd-networkd.service 2>/dev/null; then
        printf '%s' "systemd-networkd"
    else
        printf '%s' "unknown"
    fi
}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y network-manager dnsmasq iptables
systemctl enable --now NetworkManager.service
install -d -m 0755 "$CONFIG_DIR"

NETWORK_BACKEND="$(detect_network_backend)"
echo "INFO: detected network backend: $NETWORK_BACKEND"
if [[ "$NETWORK_BACKEND" == "netplan-networkd" || "$NETWORK_BACKEND" == "systemd-networkd" ]]; then
    if ! command -v netplan >/dev/null 2>&1; then
        echo "FAIL: systemd-networkd is active but netplan is unavailable; cannot configure WES safely" >&2
        exit 1
    fi
    cat > "$NETPLAN_OVERRIDE" <<'EOF'
# N0JCG WES requires NetworkManager for managed Wi-Fi and hotspot fallback.
network:
  version: 2
  renderer: NetworkManager
EOF
    chmod 0600 "$NETPLAN_OVERRIDE"
    netplan generate
    systemctl enable --now NetworkManager.service
    NETWORK_BACKEND="netplan-networkmanager"
    echo "INFO: netplan was using systemd-networkd; selected NetworkManager for WES after reboot."
fi

detect_wifi_device() {
    if [[ -n "${N0JCG_WIFI_DEVICE:-}" && -e "/sys/class/net/$N0JCG_WIFI_DEVICE" ]]; then
        printf '%s' "$N0JCG_WIFI_DEVICE"
        return 0
    fi
    nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }'
}

WIFI_DEVICE="$(detect_wifi_device)"
if [[ -z "$WIFI_DEVICE" ]]; then
    echo "FAIL: no wireless network interface was detected" >&2
    exit 1
fi

AP_SSID="${N0JCG_AP_SSID:-N0JCG-WES}"
AP_PASSWORD="${N0JCG_AP_PASSWORD:-Password}"

# Capture the infrastructure Wi-Fi identity before replacing any connection
# profiles. This remains the operator's saved LAN network while the hotspot
# is active, so the UI never mistakes the active hotspot for the LAN.
LAN_CONNECTION=""
LAN_SSID="${N0JCG_WIFI_SSID:-}"
LAN_PASSWORD="${N0JCG_WIFI_PASSWORD:-}"
LAN_CONNECTION="$(nmcli -t -f DEVICE,TYPE,CONNECTION device status 2>/dev/null | awk -F: -v device="$WIFI_DEVICE" '$1 == device && $2 == "wifi" && $3 != "--" { print $3; exit }')"
if [[ -n "$LAN_CONNECTION" && "$LAN_CONNECTION" != "$HOTSPOT_CONNECTION" ]]; then
    if [[ -z "$LAN_SSID" ]]; then
        LAN_SSID="$(nmcli -g 802-11-wireless.ssid connection show "$LAN_CONNECTION" 2>/dev/null || true)"
    fi
    if [[ -z "$LAN_PASSWORD" ]]; then
        LAN_PASSWORD="$(nmcli -s -g 802-11-wireless-security.psk connection show "$LAN_CONNECTION" 2>/dev/null || true)"
    fi
fi

if [[ "${1:-}" != "--noninteractive" && "$FROM_ENV" != "1" ]]; then
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
N0JCG_AP_SSID=$(printf '%q' "$AP_SSID")
N0JCG_WIFI_DEVICE=$(printf '%q' "$WIFI_DEVICE")
N0JCG_NETWORK_BACKEND=$(printf '%q' "$NETWORK_BACKEND")
N0JCG_AP_ADDRESS=$AP_ADDRESS
N0JCG_AP_DHCP_RANGE=$AP_DHCP_RANGE
N0JCG_USB_ADDRESS=$USB_ADDRESS
N0JCG_WIFI_DISABLED=1
N0JCG_AUTO_HOTSPOT=1
N0JCG_WIFI_SSID=$(printf '%q' "$LAN_SSID")
N0JCG_WIFI_CONNECTION=$(printf '%q' "$LAN_CONNECTION")
EOF
chmod 0644 "$CONFIG_FILE"

SECRETS_FILE="$CONFIG_DIR/network-secrets.conf"
cat > "$SECRETS_FILE" <<EOF
N0JCG_WIFI_PASSWORD=$(printf '%q' "$LAN_PASSWORD")
N0JCG_AP_PASSWORD=$(printf '%q' "$AP_PASSWORD")
EOF
chmod 0600 "$SECRETS_FILE"

nmcli connection delete "$WIFI_CONNECTION" >/dev/null 2>&1 || true
nmcli connection delete "$HOTSPOT_CONNECTION" >/dev/null 2>&1 || true
nmcli connection delete "$USB_CONNECTION" >/dev/null 2>&1 || true

nmcli connection add type wifi ifname "$WIFI_DEVICE" con-name "$HOTSPOT_CONNECTION" ssid "$AP_SSID"
nmcli connection modify "$HOTSPOT_CONNECTION" 802-11-wireless.mode ap 802-11-wireless.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$AP_PASSWORD" ipv4.method shared ipv4.addresses "$AP_ADDRESS" ipv4.shared-dhcp-range "$AP_DHCP_RANGE" ipv6.method disabled connection.autoconnect yes

nmcli connection add type ethernet ifname usb0 con-name "$USB_CONNECTION"
nmcli connection modify "$USB_CONNECTION" ipv4.method disabled ipv6.method disabled connection.autoconnect no

install -m 0755 "$(dirname "$0")/n0jcg-usb-gadget.sh" /usr/local/sbin/n0jcg-usb-gadget.sh
install -m 0755 "$(dirname "$0")/n0jcg-usb-gadget-remove.sh" /usr/local/sbin/n0jcg-usb-gadget-remove.sh
install -m 0755 "$(dirname "$0")/n0jcg-usb-network.sh" /usr/local/sbin/n0jcg-usb-network.sh
install -m 0755 "$(dirname "$0")/n0jcg-network-fallback.sh" /usr/local/sbin/n0jcg-network-fallback.sh
sed 's/@APP_USER@/'"${N0JCG_APP_USER:-pi}"'/g' "$(dirname "$0")/n0jcg-usb-gadget.service" > /etc/systemd/system/n0jcg-usb-gadget.service
install -m 0644 "$(dirname "$0")/n0jcg-network-fallback.service" /etc/systemd/system/n0jcg-network-fallback.service
install -m 0644 "$(dirname "$0")/n0jcg-usb-network.service" /etc/systemd/system/n0jcg-usb-network.service

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
systemctl enable n0jcg-usb-gadget.service n0jcg-usb-network.service n0jcg-network-fallback.service
systemctl restart n0jcg-usb-gadget.service || true
systemctl restart n0jcg-usb-network.service || true
systemctl restart n0jcg-network-fallback.service
echo "PASS: hotspot configured as the startup network; local Wi-Fi is disabled until enabled by the operator"
echo "INFO: hotspot is $AP_SSID at $AP_ADDRESS with leases $AP_DHCP_RANGE"
echo "INFO: USB gadget uses static 192.168.60.1 on the Pi 4 USB-C power/data port"
echo "INFO: USB DHCP is disabled; configure the Windows USB Ethernet adapter as 192.168.60.2/24"
echo "INFO: reboot required if dwc2 was newly added to the boot configuration"
