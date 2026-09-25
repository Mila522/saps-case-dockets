# Authentication and authorization

## Current flow: password plus email verification

Registration creates an active but **unverified, pending** account, complainant
profile and COMPLAINANT role. No access or refresh tokens are issued yet. A random
six-digit email code must be verified to finish signing in. Subsequent sign-ins
require a valid password and a newly submitted email code. This applies to all
roles, including charge officers, commanders and investigators.

This is password plus email verification. It is **not phishing-resistant** and is
not equivalent to a strong authenticator or passkey factor; email compromise can
compromise this verification channel. It intentionally differs from the
presentation's authenticator-MFA wording. The PDF has not been modified.

## Configuration and startup

Keep `.env` local and untracked. Configure `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USE_STARTTLS=true`, `SMTP_USERNAME`, `SMTP_PASSWORD` (the sender's app
password), and `EMAIL_FROM`. `.env.example` contains placeholders only. Existing
`JWT_SECRET_KEY` and `MFA_ENCRYPTION_KEY` remain required; retain the latter to
verify existing users' TOTP factors during transition. SMTP configuration is read
by Settings with secret/connection fields excluded from representations.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Migration `eabef524c593`, following `089c746ed6a9`, adds `user_email_auth` and
`email_auth_challenges` and grants the runtime role access. It does not rewrite
applied migrations, backfill verification, reset tables, or change accounts,
roles, cases or audit history. Run it on each deployment before restarting the
application. No tables are created at application startup.

## Endpoints under `/api/v1/auth`

| Endpoint | Behavior |
| --- | --- |
| POST /register | Existing registration fields; 201 EMAIL_CODE_REQUIRED challenge, masked recipient, five-minute expiry, resend cooldown and SMTP acceptance status |
| POST /login | Username/email and password; EMAIL_CODE_REQUIRED or TOTP_TRANSITION_REQUIRED for protected legacy accounts |
| POST /email/verify | challenge_token and code; consumes the challenge and returns normal access/refresh tokens |
| POST /email/resend | challenge_token; returns a replacement challenge and invalidates every earlier email challenge for the account |
| POST /email/transition | Legacy password challenge and current TOTP code; authorizes a pending email transition, never issues access tokens |
| POST /refresh | Existing opaque refresh token rotation/reuse protection |
| POST /logout | Existing session-family revocation |
| GET /me | Safe user summary and current database roles/permissions |

Retired `/mfa/setup` and `/mfa/verify-setup` return 410. Deprecated `/mfa/verify`
is an alias for the legacy transition endpoint; it no longer issues normal tokens.
No new authenticator enrollment or secret/QR display is available. PyOTP and
Fernet remain necessary for existing-account transition; no QR dependency was
found in the application requirements.

## Existing accounts: explicit transition

TOTP enrollment historically did **not** verify email ownership. No existing
`is_verified` value alone is trusted as an email authentication method.

1. An account with an active TOTP method signs in with its password.
2. The transition-only screen asks for the existing factor once. It displays no
   setup key or QR code. Without this proof, email cannot replace the factor.
3. A code is submitted to the account's recorded email address. The TOTP method
   remains active until that code is successfully verified.
4. Successful email verification records the exact verified address/time,
   deactivates the TOTP method, revokes previous sessions and issues a new session.
   Future logins use password plus email codes.

If the user cannot access their existing factor or recorded mailbox, keep the
account protected. An administrator must complete independently verified account
recovery before a controlled migration; no self-service factor reset, arbitrary
email-change or recovery bypass endpoint is introduced here. Do not solve this
by toggling `mfa_enabled` or `is_verified`. Existing active TOTP sessions remain
valid until expiry/revocation or successful transition. Existing accounts without
an enrolled active factor and without an enabled-method flag must prove their
password and email ownership. Accounts with an enabled-method flag but missing or
mismatched method state require administrator-assisted recovery; flags alone are
never treated as proof.

## Security and transactions

Codes use `secrets.randbelow`, expire in exactly five minutes, and are stored as a
domain-separated HMAC-SHA256 keyed by the existing high-entropy JWT server secret.
The digest binds account, session/challenge, purpose, recipient and code. No
plaintext code or unkeyed code hash is stored. Rotating the signing secret
invalidates outstanding challenges/tokens. The backing challenge session lasts
30 minutes to allow resend after code expiry; it never works as an access or
refresh credential. Resend returns a new token; clients must replace the old one.

All code issuance, resend, verification and account transitions serialize on the
user row, with session locks following it. A consumed challenge cannot succeed
concurrently a second time. Resend revokes all older email challenges across tabs.
Code failures are counted from persisted sanitized audit records across challenges
and purposes in the existing account-lock window. Password success and resend do
not erase that budget. Defaults are five failures and a 15-minute account lock.
SMTP attempts, including failed/ambiguous attempts, have a per-account 60-second
cooldown and ten-per-hour limit. Limits apply to fresh logins as well as resend.
429 responses include Retry-After. Deployment still requires ingress/IP rate
limits against registration floods across many distinct accounts.

`user_email_auth` explicitly binds an account to its verified address and timestamp.
Access dependencies and refresh require an active account, the existing compatibility
flag, and either a matching verified email method or the retained active legacy
TOTP method. Setting `mfa_enabled=true` alone grants no access. `is_verified` is
set only after an email code succeeds. Roles, permissions, station restrictions,
assignment checks, session rotation, logout and immutable case history remain intact.

SMTP uses certificate-verified STARTTLS, a ten-second connection/socket timeout,
and no debug logging. SMTP acceptance is **not confirmed inbox delivery**. Failed
or ambiguous submissions invalidate the attempted code and persist the cooldown;
they issue no tokens. A newly registered account remains pending so the user can
return to sign-in and retry after the cooldown. If SMTP accepted but the database
commit failed, the unusable message may still arrive; sign in again for a new code.
Never log SMTP exceptions, message bodies, passwords, codes, tokens or credentials.

Authentication mail is separate from D's case notifications. D's backend, SMS,
confirmations, alerts and dashboards are not redesigned or represented as complete.
Audit events contain fixed action names and user/session IDs only. Existing password
hashing, lockouts, generic password failures, sanitized validation errors, HTTPS
requirements, no-store responses, live RBAC and refresh-family revocation remain.

## Local walkthrough

1. Apply the migration, restart the server and open `/portal/`.
2. Register using an email inbox you control. Verify that the pending screen shows
   a masked address, expiry guidance and resend countdown.
3. Enter the emailed code. An incorrect code keeps the form open; a successful
   code opens My complaints. Sign out, then sign in with password and a fresh code.
4. Use `/officer/` for charge officers/commanders and `/investigator/` for investigators.
   Existing TOTP accounts first complete the one-time protected transition above.
5. If mail submission fails, inspect local configuration without posting secrets.
   Wait for the cooldown and return to sign-in. A delayed message from a failed
   attempt is intentionally invalid. No real delivery test was performed by Codex.

## Automated checks

Tests use an in-memory fake authentication mail adapter globally: no real emails.
The standalone headless Edge check also installs that adapter before making any
registration/login request. General regression fixtures bypass the waiting period
only inside tests; dedicated tests restore production limits and manipulate fixture
timestamps. PostgreSQL concurrency tests use disposable migrated databases and
separate connections. Ordinary API/browser fixtures roll back their records.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
node --test tests/frontend/*.test.mjs
.\.venv\Scripts\python.exe scripts/check_investigator_browser.py
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

A owns `app/modules/authentication/*`, the new migration, settings, `.env.example`,
this document and authentication tests. Shared integration changes cover
`app/frontend/app.mjs`, `frontend/officer/{index.html,assets/app.js}`,
`frontend/investigator/assets/app.mjs` and the browser script. Case business modules
and D-owned modules are unchanged.

## Verification in this checkout (2026-09-25)

The new migration has been applied to the configured local PostgreSQL database;
Alembic reports `eabef524c593` at head and no pending schema operations. The fake-mail
headless browser check passes A registration/login and the shared B/C staff login,
including an incorrect code without lost form progress, then the existing case,
evidence, custody and session workflows. Ten frontend client tests pass.

The saved SMTP settings were subsequently supplied. A standalone SMTP test was
accepted and the user confirmed receiving it; this was not a complete real-account
login test. Login/resend initially ran in a restricted process that could not open
the SMTP socket (Windows error 10013), while the standalone test ran with network
access. Run the development server from a normal local terminal with outbound
SMTP permitted, using the startup command above. Never disable TLS verification
to work around network restrictions. Restart after saving changes to `.env`.

Resend buttons now show sending/countdown feedback, serialize verification and
resend requests, honor the server's Retry-After value, and retain a back-to-sign-in
option when a challenge is unusable. The complainant buttons use a spaced,
responsive layout. Browser tests exercise resend with delayed fake mail.

Final full Python suite: **214 passed**, two existing Starlette/AnyIO dependency
deprecation warnings, 96.81 seconds. **10 frontend tests passed**. Browser workflows,
Alembic current/check and `git diff --check` passed. No automated test remains
failing or skipped due to unavailable services. Real SMTP delivery was not run.
Changes remain uncommitted on `feature/investigation-evidence-c`; nothing was
merged, pushed or reset.


Resend follow-up checks: 35 focused authentication tests, four investigator
frontend/API checks and 11 frontend client tests passed. Login/resend browser
checks passed before the broader script reached its officer queue check. The
full browser rerun was not completed: the queue-refresh test selector was
corrected, but permission for the final Edge rerun was declined. A dossier-loading
race was also fixed by binding investigator forms before loading that panel.
`git diff --check` passed. No additional real email was sent during this follow-up.
