# N0JCG Winlink Email Server email frontend

## Boundary

The webmail interface is a client-side mailbox front end. It does not act as
an RMS gateway, SMTP server, or independent Internet mail service. Pat owns
Winlink mailbox storage and transport; the N0JCG layer will provide the
authenticated browser API, state evidence, and safe queue controls.

## Initial routes

- `/webmail/` — user mailbox and compose surface.
- `/ui/` — protected operator console and radio/service diagnostics.

## Planned API surface

The browser UI is intentionally staged until these authenticated endpoints are
implemented:

- `GET /api/v1/mail/status` — Pat process, mailbox identity, freshness, and
  connection state.
- `GET /api/v1/mail/messages?folder=inbox` — authenticated normalized message summaries from Pat's mailbox API.
- `GET /api/v1/mail/messages/{id}?folder=inbox` — authenticated message body and attachment metadata from Pat.
- `POST /api/v1/mail/messages/{id}/read` — mark an authenticated Pat message read.
- `DELETE /api/v1/mail/messages/{id}?folder=inbox` — delete an authenticated Pat message.
- `POST /api/v1/mail/drafts` — save a draft locally or in the Pat mailbox.
- `GET /api/v1/mail/drafts` — list drafts for the authenticated Winlink callsign.
- `POST /api/v1/mail/queue` — validate and queue a message for transmission.
- `GET /api/v1/mail/queue` — list local queued messages for the authenticated callsign.
- `POST /api/v1/mail/queue/{id}/cancel` — cancel before modem ownership.

Every response must identify its source (`pat`, `local_queue`, or `unknown`),
include an observation timestamp, and distinguish `READY`, `QUEUED`,
`TRANSMITTING`, `DELIVERED`, `RETRYING`, `FAILED`, and `UNKNOWN`.

## Safety and truthfulness

The browser must not report delivery merely because a message was accepted into
the local queue. Delivery requires Pat evidence and a completed Packet RMS
transaction. A configured radio or detected DigiRig is not proof of delivery.
