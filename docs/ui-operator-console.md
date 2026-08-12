# N0JCG Winlink Email Server operator console

## Product surface

The web UI is branded **N0JCG Winlink Email Server** and uses the N0JCG Open Radio Platform descriptor. Internal host and repository identifiers remain `PI-WINLINK` and `N0JCG-WINLINK-EMAIL-SERVER`.

This is a client-side email appliance. It connects to a Packet RMS gateway; it is not itself an RMS gateway.

## State contract

The first UI shell uses explicit labels instead of optimistic connectivity claims:

- `Ready` means the appliance software baseline is available.
- `Detected` means the DigiRig USB interfaces were observed.
- `Unavailable` means the radio-dependent operation cannot be performed yet.
- `Unknown` means RF or PTT behavior has not been verified.

These labels are paired with text and shape, not color alone. Signal Cyan indicates live technical activity; Operational Green is reserved for verified healthy or complete states.

## Layout

The operator console uses a compact N0JCG navy header, a constrained working canvas, state cards, and a diagnostics checklist. The first viewport prioritizes appliance identity, safety/transmit state, DigiRig evidence, and the next safe action.

## Accessibility

The shell uses semantic headings, a status role, visible link labels, keyboard-visible focus, responsive single-column behavior, and reduced-motion handling. Future live data must include freshness and source information.

