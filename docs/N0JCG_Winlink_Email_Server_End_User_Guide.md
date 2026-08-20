# N0JCG Winlink Email Server

## End User Guide

**N0JCG Open Radio Platform**
**Document version:** 1.3
**Appliance host:** `PI-WINLINK`

### What this appliance does

N0JCG Winlink Email Server is a client-side Raspberry Pi appliance for sending and receiving Winlink email through an external Packet RMS gateway. It provides a branded operator console for configuration and diagnostics and a webmail interface for account owners.

The appliance is not an RMS gateway. The radio and Packet RMS gateway provide the radio path; the Raspberry Pi provides the local client, mailbox, queue, and browser experience.

## 1. Getting started

### Required hardware

- Raspberry Pi 4, 64-bit Debian/Raspberry Pi OS, with reliable power
- microSD card with the operating system installed
- DigiRig Mobile audio/PTT interface
- Compatible amateur radio with packet/data audio and PTT connections
- USB cable from DigiRig Mobile to the Raspberry Pi
- Radio interface cables appropriate for the selected radio
- Ethernet or Wi-Fi network connection for initial setup and Winlink CMS authentication
- A computer on the same network with MSYS2 Bash, Git, OpenSSH, and `sshpass`

### Useful installation accessories

- Powered USB hub when more than one USB peripheral is required
- Keyboard, display, or SSH access for recovery
- Short, labeled audio/PTT cables
- UPS or clean 5 V power source for unattended operation

### Before connecting the radio

The software can be installed and the web interfaces can be configured before the radio is connected. USB detection alone does not prove audio, serial/PTT, packet, or RF operation. Transmission remains disabled until the operator explicitly configures and verifies the radio path.

## 2. Install from MSYS2 Bash

The official deployment helper copies the complete application to the Pi, runs the installer, and verifies that the webmail service is active.

### Prepare the workstation

Open **MSYS2 UCRT64** or **MSYS2 MSYS** Bash and install the required tools if needed:

```bash
pacman -S --needed git openssh sshpass
```

Clone the repository and enter it:

```bash
git clone https://github.com/bakstaaj/N0JCG-WINLINK-EMAIL-SERVER.git
cd N0JCG-WINLINK-EMAIL-SERVER
```

### Run the deployment helper

Run the official script without arguments to use its guided prompts:

```bash
./deploy/push_to_pi.sh
```

The script prompts for:

1. Raspberry Pi IP address
2. Raspberry Pi SSH username
3. Raspberry Pi SSH password

The default values are `192.168.68.149` and `pi`, but each field can be changed for another installation.

For a repeatable deployment, provide the address and username directly:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi
```

The installer then asks for the Nginx operator-console username and password. The password is entered twice so a typing error is caught during the initial setup. Keep the operator credentials separate from the Winlink mailbox password.

### Configure Wi-Fi, hotspot fallback, and USB gadget

To configure connectivity during deployment, add `--configure-connectivity`:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi --configure-connectivity
```

The helper configures the secured `N0JCG-WES` hotspot and confirms its password. The Pi activates the hotspot at `192.168.50.1` and leases addresses from `192.168.50.100` through `192.168.50.200`.

The Pi 4 USB Ethernet gadget is also installed. Connect a computer to the Pi 4 USB-C power/data port—not a blue USB host port—to reach the appliance directly at `192.168.60.1`.

### Optional repeat deployment

The default fallback hotspot is `N0JCG-WES` with initial password `Password`; the installer prompts for confirmation and a replacement password during first-time setup.

Existing operator authentication is preserved during normal upgrades. To intentionally reconfigure the operator account:

```bash
./deploy/push_to_pi.sh 192.168.68.149 pi --configure-operator-auth
```

### Verify the installation

Open these URLs from a computer on the same network:

- Operator console: `http://PI-IP/ui/`
- User webmail: `http://PI-IP/webmail/`

Replace `PI-IP` with the address entered during deployment. The deployment helper must report that the API was deployed and the `n0jcg-webmail.service` service is active.

## 3. First-time setup

### Operator console

Sign in to `/ui/` using the operator username and password created by the installer. Use the console to review:

- Host identity and service state
- DigiRig and USB device detection
- Winlink Client readiness
- Radio profile and Packet settings
- Diagnostics and logs

The **Nearby RMS gateways** panel uses the official Winlink gateway status data. When
the Pi has Internet access, select **Refresh gateway list** to update the local
cache. In the field, the cached list remains available without Internet access.
The panel shows the five closest Packet RMS channels in a scrollable list. Enter
simulated latitude/longitude while testing, or select **Use GPS** after a USB GPS
receiver and `gpsd` are available.

Double-click a gateway row to copy its RMS target and frequency into the Packet
station profile, scroll to that profile, and focus the target field. Review the
values before selecting **Save radio profile**. The selected target is applied to
the next Winlink client exchange; it is not a gateway-side configuration change.

The operator console is protected because it can change appliance configuration and radio safety settings.

### Webmail account

Open `/webmail/` and select **Sign in with Winlink**. Enter the user’s Winlink email address and secure-login password. The appliance validates the credentials against Winlink before opening the private local mailbox for that callsign. Webmail remains closed until Winlink CMS secure login is accepted; failed or timed-out authentication never opens a session or mailbox.

After secure login succeeds, the session remains active for the browser session. Refreshing the page does not sign the user out. Close the browser or select **Log out** to end the session. Use **Log out** on shared devices.

The mailbox is isolated by callsign. A user cannot select another user’s mailbox from a URL or browser control.

## 4. Using webmail

### Inbox and folders

The Inbox is the default panel. Sent, Drafts, and Send queue are available from the left navigation. User-created folders appear indented under Inbox. Drag an Inbox message onto a custom folder to organize it.

### Compose, templates, and drafts

Select **Compose** for a blank message. Use **Choose template** at the top of the Compose form to select an installed Winlink Standard Form, fill its fields, and insert the editable message. Use **Signature** beside it to manage the per-user signature. Select **Save draft** to keep work locally on the appliance. The Drafts counter updates after saving. Open a draft to continue editing; queuing an opened draft removes it from Drafts and places it in Send queue.

### Send queue and automatic synchronization

Selecting **Queue message** stages the message into Pat’s official outbox and immediately starts a Packet RMS synchronization. The Send queue shows queued, staged, and transmission state. After a successful send, the message is removed from Send queue and its counter updates automatically. The operator configures automatic mailbox synchronization in `/ui/` under **Packet station → Automatic mailbox sync (minutes)**. The allowed range is 5 to 1440 minutes; the default is 30 minutes.

After Pat reports the final outgoing-transfer command, WES clears the transmitted
queue item promptly while the remainder of the mailbox synchronization continues.
This prevents a successful send from appearing stuck while the session closes.

### Mailbox synchronization progress

After Winlink secure login is accepted, WES opens the webmail session and continues the mailbox transfer in the background. The status message advances through the RMS connection, mailbox index, proposal, message selection, download, and finalization stages. A message such as **0 new emails were received** means the authenticated mailbox was checked and no new messages were available in that exchange. The **Refresh** control is disabled while a transfer is active so an operator cannot interrupt the current RF session.

If the radio session closes after one or more messages have been received, WES records the received messages and clears the stale in-progress state. A transport failure after authentication does not expose another user’s mailbox and does not invalidate the logged-in callsign unless authentication itself has been lost.

Attachments are limited to 100 KB. The interface warns when an attachment is larger than 10 KB because packet-radio transfer may be slow.

### Signature

Select **Signature** to save a per-user signature. The signature is stored with the Winlink callsign and is restored after the next login.

### Logout

Use **Log out** in the top navigation when leaving the appliance. Log out from both the operator console and webmail when using a shared workstation. Webmail uses a browser-session cookie by default; it is not a persistent “remember me” login.

### Bulk message actions

Inbox, Sent, and custom folders support checkboxes for selecting multiple messages. Use **Mark selected read** or **Delete selected** to apply an action to all selected messages. Moving a message to a custom folder removes it from its original folder. Opening a message marks it read and removes its unread emphasis.

## 5. Connecting the radio

After the software and account setup are complete:

1. Power off the radio and Raspberry Pi before changing audio/PTT cabling.
2. Connect the DigiRig Mobile to the radio using the correct radio-specific cables.
3. Connect the DigiRig Mobile directly to a Raspberry Pi USB 3 port or a powered USB hub.
4. Keep the Pi 4 USB-C power/data port available for the USB Ethernet gadget; it is not a DigiRig host port.
5. Power the radio, DigiRig Mobile, and Raspberry Pi.
6. Confirm that the operator console reports the expected USB/audio/serial devices as **Detected**.
7. Configure the radio profile, audio levels, PTT method, and selected Packet RMS gateway.
8. Perform receive-only checks before enabling any transmit behavior.
9. Enable transmission only after the operator has verified the radio path and local regulations.

## 6. Troubleshooting

### “Mailbox unavailable”

This means the Winlink account was authenticated, but the local mailbox service is not currently available. It is not a request for the user to type a command. Connect the radio and select **Refresh** to start a mailbox synchronization. If the message persists, the operator should review the client service and diagnostics.

### Local delete versus Winlink Webmail

The WES Inbox, folders, and delete actions manage the local appliance mailbox. Winlink Webmail is a separate CMS view and may continue to show the same server-side message after WES downloads or locally deletes it. Remove a message separately through Winlink Webmail when server-side retention also needs to change.

### A previous RF session is still active

Only one mailbox user and one RF session are allowed at a time. A new login terminates the previous session, releases the Pat/AGW connection, and allows the radio path to settle before starting again. After a failed exchange, wait for the visible failure state and the RF activity to stop before retrying. The operator diagnostics show the last client/RF event.

### Login fails or remains on “Validating through the Winlink client…”

Confirm the Winlink address and secure-login password. Check the Pi network connection, radio power, DigiRig cabling, frequency, packet station ID, RMS target, and system time. The login screen intentionally remains open until secure login is accepted; no mailbox is exposed during this wait. A failed RF exchange returns the user to the login screen. Repeated failed attempts are rate-limited for protection.

### Device is detected but not ready

Detection only proves that the operating system saw a USB device. Check the correct radio cable, audio routing, serial/PTT configuration, and radio power. Do not enable transmit based on USB detection alone.

### Deployment verification fails

From MSYS2 Bash, rerun the deployment helper and confirm the Pi IP address, SSH username, and SSH password. If the operator account must be changed, use `--configure-operator-auth`. For diagnostics:

```bash
./deploy/inspect_pi_webmail.sh
```

## 7. Safety and operating boundary

N0JCG Winlink Email Server is a client-side email appliance. It does not replace an RMS gateway and it must not transmit merely because a USB device is present. Radio operation requires an explicit operator configuration, a valid radio profile, a selected Packet RMS gateway, bounded PTT behavior, and receive/transmit verification.

Use the operator console for configuration and diagnostics. Use webmail for mailbox access. Do not share Winlink passwords or operator-console credentials.

## Quick reference

| Task | Location |
|---|---|
| Deploy or upgrade | `./deploy/push_to_pi.sh` in MSYS2 Bash |
| Diagnostics | `./deploy/inspect_pi_webmail.sh` |
| Operator console | `http://PI-IP/ui/` |
| User webmail | `http://PI-IP/webmail/` |
| Default SSH user | `pi` |
| Product host role | `PI-WINLINK` |
| Maximum attachment | 100 KB |
| Attachment warning | Above 10 KB |
| Automatic mailbox sync | Operator setting, 5–1440 minutes; default 30 |
| RMS gateway finder | Cached official list; closest five shown; GPS or simulated location |
| Login security | Webmail opens only after Winlink secure-login acceptance |

*N0JCG Winlink Email Server | N0JCG Open Radio Platform | Client-side appliance, not an RMS gateway*
