# N0JCG Winlink Appliance Architecture

## Ownership

### Upstream radio and Winlink components

- **Pat** owns Winlink mailbox storage, message composition/reading, and Winlink transport sessions.
- **Dire Wolf** owns software packet modulation/demodulation and KISS/AGW interfaces.
- **Hamlib** provides optional CAT/radio control.
- **ALSA/udev** provide DigiRig audio and device discovery.

### N0JCG appliance layer

- Owns the operator and user web front ends.
- Owns configuration profiles and validation.
- Owns health states and evidence collection.
- Owns transmit policy and bounded PTT authorization.
- Owns service lifecycle, logs, backups, and recovery.

## Health states

The UI must distinguish these states:

- `ABSENT`: expected hardware or service is not present.
- `DETECTED`: USB/device enumeration succeeded.
- `READY`: software configuration is valid and the service is available.
- `VERIFIED`: a specific test produced expected evidence.
- `UNKNOWN`: no reliable evidence exists.
- `FAULT`: a check failed or a service reported an error.

USB enumeration is never promoted directly to `VERIFIED` PTT or RF operation.

## Pre-radio milestone

Before the radio is connected, the appliance can verify:

1. Host identity and storage.
2. DigiRig audio, serial, and HID enumeration.
3. ALSA capture/playback device access.
4. Dire Wolf startup and configuration parsing.
5. KISS/AGW local socket behavior using a simulated packet source.
6. Pat startup, mailbox creation, and Telnet/CMS operation.
7. Browser/API health and log collection.

Actual audio level calibration, PTT closure, RF packet exchange, and RMS Packet gateway success remain hardware-gated.

