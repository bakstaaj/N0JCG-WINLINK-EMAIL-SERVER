# Webmail registration and login

## User identity

The webmail account identity is the user’s Winlink radio email address, normally
in the form `YOURCALL@winlink.org`, plus the Winlink secure-login password. This
is not an N0JCG license key and is not the Nginx operator password.

## Validation boundary

Registration and login must call Pat, which is the appliance’s Winlink client.
The backend must configure a temporary or account-scoped Pat validation attempt
and accept the account only after Winlink authentication succeeds. A local form
submission, matching password fields, or a configured DigiRig is not proof of
mailbox ownership.

The backend must:

1. Normalize and validate the Winlink address and callsign format.
2. Store only a salted password verifier for the N0JCG web session account.
3. Store the Winlink credential only in the protected Pat credential store, or
   use a short-lived validation handoff that does not persist it in browser
   storage or logs.
4. Create a session bound to the validated Winlink address.
5. Refuse mailbox reads unless the session, Pat account identity, and mailbox
   identity match.
6. Clear credentials from process memory after the validation attempt where the
   Pat integration permits it.

## API contract

- `POST /api/v1/auth/register` — accepts `email` and `password`; returns
  `202 Pending validation` until Pat completes validation.
- `POST /api/v1/auth/login` — accepts `email` and `password`; returns a secure,
  HttpOnly session cookie only after validation.
- `POST /api/v1/auth/logout` — invalidates the session.
- `GET /api/v1/auth/session` — returns session identity and validation freshness,
  never the password.

The mailbox API must use the authenticated session identity when calling Pat;
the browser must not select an arbitrary mailbox or callsign in a message URL.

## Radio limitation

Winlink credential validation may use a CMS/Telnet path where configured, but
that does not prove Packet-RMS radio delivery. Packet validation and message
delivery remain separate evidence states: `AUTHENTICATED`, `QUEUED`,
`TRANSMITTING`, `DELIVERED`, `FAILED`, and `UNKNOWN`.
