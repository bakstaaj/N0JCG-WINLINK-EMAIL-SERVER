# N0JCG Winlink Email Server

N0JCG Winlink Email Server is a Raspberry Pi client-side appliance for sending and receiving Winlink email through a Packet RMS gateway. It uses a DigiRig Mobile, a connected radio, Dire Wolf, and Pat.

The repository and host use compatibility identifiers such as `N0JCG-WINLINK-EMAIL-SERVER` and `PI-WINLINK`. The visible product identity is **N0JCG Winlink Email Server**.

## Current milestone

The target host is `PI-WINLINK`. The radio is not yet connected, so this project currently validates the non-RF foundation:

- Pat (`pat-winlink`) installed for the ARM64 Debian host.
- Dire Wolf 1.7 installed as the software AX.25 modem/TNC.
- AX.25 tools, Hamlib, SQLite, and Nginx installed.
- DigiRig USB audio, CP210x serial, and HID interfaces detected.
- Radio services remain inactive and transmit behavior remains disabled.

## Components

```text
Browser
  +-- Operator console / diagnostics
  +-- User webmail
          |
N0JCG management API and policy layer
          |
Pat (Winlink client mailbox and transport)
          |
Dire Wolf (AX.25/KISS or AGW packet modem)
          |
DigiRig Mobile -> radio -> Packet RMS gateway
```

Pat and Dire Wolf remain upstream components. N0JCG owns configuration, health evidence, safe-operation policy, logs, backups, and the browser experience.

## Brand implementation

- `branding/README.md` records the product-specific application of the N0JCG Brand Guide.
- `branding/tokens.css` contains the canonical web design tokens used by the UI.
- `branding/N0JCG_Winlink_Email_Server_Product_Brief.md` is the editable product documentation baseline.
- `ui/` contains the state-first operator console shell and webmail entry point.

Use `N0JCG` exactly as written. Use **N0JCG Winlink Email Server** on visible product surfaces, while retaining repository, service, API, and runtime identifiers for compatibility.

## Validation

Run the dependency-free scaffold check with:

```bash
python3 tools/validate_appliance.py
```

The check validates the example schema, required safety defaults, branding sources, and required project directories. It does not claim RF or gateway success.

## First deployment

Run `deploy/install_static_ui.sh` on the Pi for the initial web setup. The
installer provisions the static UI, installs `apache2-utils` when needed, and
then requires an operator username, password, and matching password
confirmation. Later runs preserve the existing operator account.

From an MSYS2 Bash terminal, `deploy/push_to_pi.sh` copies the complete source
set to the Pi and starts the installer remotely:

```bash
./deploy/push_to_pi.sh
```

The helper prompts for the Raspberry Pi IP address and SSH username, defaulting
to `192.168.68.149` and `pi`. For repeatable deployment, provide them directly:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi
```

To configure preferred Wi-Fi, the secured fallback hotspot, and the Pi 4 USB
Ethernet gadget during installation:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi --configure-connectivity
```

The helper prompts for the preferred Wi-Fi SSID/password and confirms the
fallback hotspot password. If Wi-Fi cannot obtain a DHCP address, the Pi
activates the `N0JCG-WINLINK-SETUP` hotspot at `192.168.50.1` and leases
addresses from `192.168.50.100` through `192.168.50.200`.
The USB gadget is available as a direct Ethernet connection at
`192.168.60.1` through the Pi 4 USB-C power/data port; the blue USB host ports
are not gadget ports.

The helper uses the MSYS2 `sshpass` package and prompts once for the Pi SSH
password. For a scripted run, set `N0JCG_PI_PASSWORD` in the current shell;
the helper consumes it without placing it in the SSH command line.

To intentionally reconfigure the Nginx operator account during an upgrade:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi --configure-operator-auth
```

The Pi diagnostic helper uses the same connection prompts:

```bash
./deploy/inspect_pi_webmail.sh
```

## Safety boundary

The appliance must not enable transmission merely because a USB device is present. RF operation requires an explicit operator enable, a configured radio profile, a selected Packet RMS gateway, and bounded PTT policy. USB/audio/serial detection is hardware evidence only; it is not proof of PTT or RF operation.
