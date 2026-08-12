# N0JCG Winlink Email Server Pi 4 platform baseline

## Verified host

The current appliance host reports:

| Field | Verified value |
| --- | --- |
| Board | Raspberry Pi 4 Model B Rev 1.5 |
| SoC | Broadcom BCM2711 |
| Architecture | `aarch64` |
| Memory | 2 GB class; 1.8 GiB visible to Linux |
| Operating system | Debian GNU/Linux 13 (Trixie) |
| Kernel | `6.18.34+rpt-rpi-v8` |
| Raspberry Pi firmware | `288930ab4712b99596f32732664aaaeb881ef1e0` |
| Host role | `PI-WINLINK` |

## Assessment

There is no current evidence of an incorrect operating-system installation. The device tree identifies the board as a Pi 4, the kernel is the 64-bit Raspberry Pi `rpi-v8` family, and Raspberry Pi firmware tools are available. Debian 13 ARM64 is appropriate for the installed Pat, Dire Wolf, AX.25, and Hamlib packages.

The earlier Pi 5 assumption was incorrect. It should not guide hardware selection or performance claims.

## Appliance implications

- Keep the first release lightweight and service-oriented.
- Avoid treating `aarch64` or `rpi-v8` as a board model; use the device-tree model for board identity.
- Keep Pat, Dire Wolf, the management API, and webmail independently restartable.
- Do not allocate the RTL-SDR devices to a background service unless a Winlink feature explicitly needs them.
- Retain the no-radio startup path: the UI and diagnostics must remain available when the radio is disconnected.

