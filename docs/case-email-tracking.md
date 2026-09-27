# Case email notifications and signed-in tracking

Case messages use EMAIL through the existing SMTP adapter. No SMS provider or
SMS endpoints are installed. Historical SMS rows, phone numbers, channel values
and applied tables/migrations are preserved. Old anonymous email challenge tables
also remain solely for schema/data compatibility; their endpoints and worker are
removed, and existing codes no longer grant access.

## Configuration and local execution

Reuse `SMTP_HOST`, `SMTP_PORT` (587 by default), `SMTP_USE_STARTTLS=true`,
`SMTP_USERNAME`, `SMTP_PASSWORD` and `EMAIL_FROM` from the ignored local environment.
No credentials belong in source control or application logs. Set `CASE_PORTAL_URL`
to the normal `/portal/` sign-in page, using HTTPS outside local development.
The development default is `http://127.0.0.1:8000/portal/`. Blank configuration
fails closed. No arbitrary redirect URL, query string, credentials or secret can
be configured in a case link. The old `PUBLIC_TRACKING_URL` setting is unused.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m app.modules.authentication.check_smtp
.\.venv\Scripts\python.exe -m app.modules.communications.case_email --limit 100
```

Run the server and worker from a normal local terminal with SMTP network access.
`check_smtp` checks STARTTLS and SMTP authentication without sending mail; it
prints only fixed phases, exception types and numeric error codes. It never prints
credentials, provider response text, verification codes or case message bodies.
The worker command sends real queued emails. Schedule it regularly for ongoing
case delivery; it is not started automatically or invoked by tests. Login codes
are sent synchronously by the sign-in flow and do not depend on this worker.

## Events and content

Additional EMAIL intents are committed with each existing case workflow event:

| Event | Safe email content |
| --- | --- |
| Complaint submitted | Reference, receiving station and submitted status |
| Charge Officer accepts and creates docket | Reference, CAS, PENDING_APPROVAL; explicitly states approval is pending |
| Station Commander approves docket | Reference, CAS and approval confirmation |
| Investigator assigned | Reference, CAS and current case status |
| Case moves to ACTIVE, ON_HOLD or CLOSED | Reference, CAS and resulting status; no reason text |
| Official feedback published/corrected | Fixed official-update notice, reference, CAS and status; no feedback free text |
| Refusal recorded | Reference and fixed refusal/escalation outcome; no officer notes |

Every email links to `CASE_PORTAL_URL#complaint=<UUID>` and asks the complainant
to sign in. The UUID is navigation, never authorization. The portal runs normal
password-plus-email-code MFA before calling the existing owner-scoped complaint
tracking API. Missing login is rejected; another complainant's UUID is inaccessible.
No magic link or anonymous reference lookup exists. Legacy `/portal/tracking.html`
only directs users to normal sign-in. Pending legacy email footers are replaced
with the authenticated case link before sending; sent historical records stay intact.

No internal notes, evidence, witnesses, personal contacts or unapproved investigator
names are emailed. Recording internal notes alone is not a public progress event;
use official feedback for a complainant update. Existing IN_APP messages, audits,
permissions, identifier allocation, history protection and login MFA remain intact.

## Verified recipients and accurate delivery states

The active linked account's email, `UserEmailAuth.verified_email` and complainant
email must match. A legacy verified flag or matching unlinked walk-in address is
not email proof. Unverified walk-ins still save normally and show EMAIL_NOT_VERIFIED
(not sent). The complainant portal and authorized station complaint dialog show
delivery status; recipient addresses and message bodies are never returned there.

The outbox uses unique event/source keys and PostgreSQL locks to deduplicate
retries. Status events use immutable status-history IDs. Workers commit a claim
before contacting SMTP, recheck email proof and append delivery attempts; attempts
are never edited. Case actions are not rolled back by later SMTP failure.
SENT means SMTP acceptance, not confirmed inbox arrival.

Definite SMTP connection/configuration/authentication failures or explicit
rejections can be retried after the cause is resolved:

```powershell
.\.venv\Scripts\python.exe -m app.modules.communications.case_email --retry <notification-uuid> --limit 100
```

Accepted, changed-address, unverified and uncertain sends cannot be retried with
this command. A crash or lost response during DATA stays PROCESSING for operator
reconciliation; withholding ambiguous retries avoids duplicates. SMTP does not
provide exactly-once delivery across crashes. Stable Message-ID is correlation,
not a guarantee. No automatic backfill of historic case notifications occurs.

## Staff sign-in diagnosis and recovery

The confirmed local blocker was an SMTP connection denied inside the execution
sandbox (Windows PermissionError 10013). Using the same configuration outside the
sandbox completed STARTTLS and SMTP authentication without sending mail. No missing
credentials were found. Run the app outside that restricted network environment.
This check confirms connectivity/authentication, not inbox delivery.

The shared adapter also now distinguishes DATA acceptance from connection teardown:
a QUIT error after successful acceptance does not invalidate an issued login code.
Unconfirmed/rejected sends still revoke the attempt and older challenges, persist
rate limits, return no tokens, and require a fresh password sign-in. Failed resends
return the staff UI to sign-in instead of offering an unusable old code form.
Errors remain sanitized and include Retry-After recovery guidance. No MFA bypass
or credential/password reset was introduced.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/frontend/*.test.mjs
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

All automated delivery is mocked. Tests cover workflow triggers, CAS/approval
wording, private text exclusions, ownership-protected links, failed sends and
recovery for both staff roles, immutable attempts, concurrent worker deduplication,
and removal of anonymous tracking endpoints. PostgreSQL fixtures roll back test
records; concurrency fixtures use a separate disposable database and never reset
the application's data. No new migration is needed for this revision.

## Walk-in consent, contact details and station invitations

For new walk-in complaints, the complainant first registers and completes email
verification at `/portal/`. The charge officer enters that account email and
requests an account-link consent code in the intake dialog. The complainant gives
that separate five-minute code only to the officer recording the complaint.
Submitting it links the new complaint to the verified profile and updates the
supplied phone/residential address with that consent. Account identity/email,
password, role and verification settings are not changed. Residential and incident
addresses remain separate. No account is linked from email equality alone.

Consent requests require an active charge officer/station assignment. Codes are
hashed, single-use, bound to officer/station/email, expire in five minutes, allow
five guesses, and have a 60-second recipient cooldown plus hourly limits. Failed
SMTP submission cannot authorize a link. Existing unlinked historical walk-in
records are not silently moved to an account. Intake without a verified account
still works but explicitly reports that email was not sent.

The assigned investigator can view complainant name, saved email, phone and
residential address from the docket detail screen. These reads are audited and
check current assignment/station on every request. Identification numbers and
unrelated account information are not returned.

The station invitation form schedules an interview or meeting. Email contains
the appointment in SAST, receiving station/address, CAS and protected tracking
link. It does not copy internal notes or free-form investigation content. Each
invitation is saved as immutable official feedback and uses a request UUID to
prevent duplicate emails on retries. Closed cases and unassigned investigators
cannot send invitations. The portal's complaint detail page displays case status
and official updates/appointments after normal sign-in and ownership checks.

Run the case worker continuously for actual assignment, opening and invitation
emails (this sends real pending case notifications):

```powershell
.\.venv\Scripts\python.exe -m app.modules.communications.case_email --watch
```

API case actions enqueue rather than synchronously send. PENDING is queued,
SENT means SMTP acceptance, and FAILED/PROCESSING remain subject to the documented
safe-retry policy. Real complainants outside the developer machine need a public
HTTPS `CASE_PORTAL_URL`; localhost links work only on that machine.
