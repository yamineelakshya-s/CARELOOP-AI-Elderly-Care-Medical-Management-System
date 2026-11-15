# CareLoop AI

## Local development

Install the requirements in the existing virtual environment, create a local
`.env` from `.env.example`, then create an invitation for the first elder
account:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.create_invitation --name "Account owner"
```

The generated 8-character code is one-time and expires after seven days. The
elder can sign in at `/login` and invite caregivers or family members from the
dashboard, choosing exactly which record categories each invitee may access.
Care-circle invitations are limited to caregiver and family roles.
Each record category has separate view and manage grants. Elders retain full
control of their own circle; members can only access the circles and features
explicitly granted to them.
Keep `SESSION_SECRET` stable while invitations are pending: invitation hashes
are keyed with it, so rotating the secret invalidates pending invitations and
existing sessions. Issue new invitations after a rotation.

After joining, users can sign in with their email and a password of at least
8 characters. They may enter these credentials while joining or set them up
from the dashboard while signed in. Existing accounts created before password
sign-in was added keep their data and can set credentials from the dashboard.
If an existing account is already signed out, a local operator can list
accounts and set up its sign-in details:

```powershell
.\.venv\Scripts\python.exe -m app.set_account_credentials --list
.\.venv\Scripts\python.exe -m app.set_account_credentials --user-id <USER_ID>
```

Passwords are stored as salted PBKDF2 hashes. Five consecutive failed
sign-in attempts trigger a 15-minute in-memory lockout. Email ownership
verification and self-service password reset are not implemented, so this is
not a production identity-management system.

The app also provides appointments, caregiver relay follow-ups, elder approval
requests, in-app notifications, profile editing, and a persistent care timeline.
All records remain in the configured SQLite database and are scoped by
care-circle permissions. Caregiver Relay supports dose-linked claim/contact/
resolve/reopen actions. Medication proposals are applied only after the elder
approves them. The dashboard calculates adherence from actual dose records for
the selected week.

SQLite schema migrations run automatically on application startup and are
additive. Existing user, medication, appointment, care-circle, and session
records are not reset. Existing timezone-naive appointment and dose timestamps
are treated as UTC by reminder processing; new timestamps are stored in UTC.

## AI care summary

The local summary is generated from the signed-in user's authorized records.
Gemini integration is optional: set `GEMINI_API_KEY` in the local `.env` to
request general, non-medical guidance. The Gemini request contains only a
generic prompt and the account role; account names, medication details,
appointments, and other care records are not sent. Without a key, the local
summary remains available.

Run the app and tests with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
.\.venv\Scripts\python.exe -m pytest -v
```

The local SQLite database is stored under `data/` and is ignored by Git.
Production must set a unique `SESSION_SECRET` of at least 32 characters and
`TICK_SECRET` of at least 32 characters. The authentication lockout is
process-local and is not shared across multiple app workers.

## Email reminders and scheduled tick

Email reminders use the Brevo transactional email API. Set these optional
variables in `.env`:

```text
BREVO_API_KEY=
SENDER_EMAIL=
TICK_SECRET=
APP_BASE_URL=http://localhost:8000
```

Without Brevo configuration the app records email attempts as skipped and
continues to create eligible in-app notifications and overdue-dose Relay
alerts. Failed and skipped email events can be retried; a successfully logged
event is not sent again. Email HTTP requests have a short timeout. Tests mock
the mailer and do not send real messages.

Configure an external scheduler to POST `/api/tick` about once per minute with
the `X-Tick-Secret` header. The endpoint sends pending-dose reminders about
10 minutes before a dose and appointment reminders about 24 hours before an
appointment. After a pending dose is unconfirmed for 15 minutes, it creates a
dose-linked Relay alert and notifies members who have medication or Relay view
permission. The wording describes the dose as unconfirmed and does not claim
that it was missed.

Example PowerShell request (after setting `TICK_SECRET` in the shell):

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/tick" `
  -Headers @{ "X-Tick-Secret" = $env:TICK_SECRET }
```

Brevo email was not tested unless a real API key and verified sender are
configured. Do not put credentials in source control or command history.

## Docker and deployment preparation

Build and run locally with a named volume so SQLite data persists:

```powershell
docker build -t careloop-ai .
docker run --name careloop-ai `
  --env-file .env `
  -e DATABASE_PATH=/data/careloop.sqlite3 `
  -p 8000:8000 `
  -v careloop-data:/data `
  careloop-ai
```

Check `http://127.0.0.1:8000/healthz`. The container starts as a non-root user.
The image intentionally excludes `.env` and the local database. SQLite data on
hosting platforms with ephemeral filesystems will be lost on replacement or
restart unless a persistent volume is configured. No hosting provider or
persistent database has been configured by this project.

## Optional synthetic demo accounts

Set `ENABLE_DEMO_ACCOUNTS=true` in the local `.env` to enable the synthetic
accounts:

| Name | Role | Join code |
| --- | --- | --- |
| Lakshmi | Elder | `LAKSHMI1` |
| Meena | Caregiver | `MEENA001` |
| Arjun | Family | `FAMILY01` |

These are not real accounts and have no seeded health records. Demo accounts
are disabled by default and cannot invite real users.