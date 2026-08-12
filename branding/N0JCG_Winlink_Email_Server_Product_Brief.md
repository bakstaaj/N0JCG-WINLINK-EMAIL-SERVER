# N0JCG Winlink Email Server

## Product brief

**Product:** N0JCG Winlink Email Server  
**Descriptor:** N0JCG Open Radio Platform  
**Host role:** PI-WINLINK  
**Status:** Pre-radio integration foundation  
**Owner:** N0JCG Open Radio Platform

N0JCG Winlink Email Server is a client-side Raspberry Pi appliance for sending and receiving Winlink email through a Packet RMS gateway. It combines Pat, Dire Wolf, DigiRig Mobile hardware, and a connected radio behind a clear operator and webmail interface.

## Capability boundary

The current foundation validates the host, software packages, DigiRig USB interfaces, configuration schema, and web UI shell. It does not claim radio PTT, RF packet exchange, RMS gateway reachability, or end-to-end email delivery until the radio is connected and tested.

## Operator language

Use these labels in the UI and documents:

- **Detected:** the operating system observed a device.
- **Ready:** software configuration is valid and the service can start.
- **Verified:** a defined test produced expected evidence.
- **Unknown:** the system has no reliable observation.
- **Unavailable:** the feature or hardware is absent or disabled.
- **Fault:** a check failed and recovery is needed.

Never label a DigiRig as `Connected` when only USB enumeration has been observed. Use `Detected` until audio, serial/PTT, and radio behavior are individually verified.

