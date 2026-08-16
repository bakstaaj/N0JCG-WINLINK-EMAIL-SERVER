# Webmail registration and login

## User identity

The webmail identity is the user’s Winlink radio email address, normally in the
form `YOURCALL@winlink.org`, plus the Winlink secure-login password. There is no
separate N0JCG webmail registration account. This is not an N0JCG license key
and is not the Nginx operator password.

## Validation boundary

Login calls Pat, which is the appliance’s Winlink client. On first login, the
backend initializes a private mailbox directory for the callsign, then runs an
account-scoped Pat validation attempt. The account is accepted only after
Winlink authentication succeeds. A local form submission, matching password
fields, or a configured DigiRig is not proof of mailbox ownership.

The backend must:

1. Normalize and validate the Winlink address and callsign format.
2. Create or reuse only `/var/lib/n0jcg-winlink-webmail/mailbox/CALLSIGN` for
   that validated callsign, with owner-only permissions.
3. Store the Winlink credential only in the active protected Pat session, or
   use a short-lived validation handoff that does not persist it in browser
   storage, the mailbox directory, or logs.
4. Create a session bound to the validated Winlink address and callsign.
5. Refuse mailbox reads unless the session, Pat account identity, and mailbox
   identity match.
6. Clear credentials from process memory after the validation attempt where the
   Pat integration permits it.

## API contract

- `POST /api/v1/auth/login` — accepts `email` and `password`; returns a secure,
  HttpOnly session cookie only after validation.
- `POST /api/v1/auth/logout` — invalidates the session.
- `GET /api/v1/auth/session` — returns session identity and validation freshness,
  never the password.

The mailbox API must use the authenticated session identity and its isolated Pat mailbox when calling Pat;
the browser must not select an arbitrary mailbox or callsign in a message URL.

## Standard Forms

The operator can install or update the official Winlink Standard Forms library
from the Pi with:

```bash
sudo /opt/n0jcg-winlink/tools/update_standard_forms.sh
```

After installation, users can open **Templates** in Webmail, choose a form,
complete its fields, and insert the generated plain-text message into Compose.
The catalog keeps the official version and category metadata and substitutes
the signed-in callsign plus UTC date/time fields. The original HTML/JavaScript
form files are retained for future sandboxed rendering; they are not executed
inside the authenticated WES page.

The login endpoint permits five failed attempts per client address within a
15-minute window. A successful Winlink authentication clears that client's
failure window; rate-limited responses include `Retry-After` and do not reveal
whether an account exists.

## Radio limitation

Winlink credential validation may use a CMS/Telnet path where configured, but
that does not prove Packet-RMS radio delivery. Packet validation and message
delivery remain separate evidence states: `AUTHENTICATED`, `QUEUED`,
`TRANSMITTING`, `DELIVERED`, `FAILED`, and `UNKNOWN`.
