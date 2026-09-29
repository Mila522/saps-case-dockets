# System administration

Open `/officer/` and use the existing password plus emailed six-digit code flow.
Verified `SYSTEM_ADMINISTRATOR` users are routed to `/admin/`. Every admin API
requires both this role and the corresponding `user.manage` or `audit.view_all`
permission. A public HTML shell gives no access to account or audit data.

The existing role is reused. Migration `e712ac340a01` narrows its old broad seed
grants to account management and audit only. Other roles and their mappings are
unchanged. Administrators do not receive case, evidence, approval, alert or global
case-dashboard access. Do not combine administrator and case roles during setup.

## First administrator

Run migrations first, using the existing configured database:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The explicit, idempotent setup command reads `SAPS_ADMIN_USERNAME`,
`SAPS_ADMIN_EMAIL`, and `SAPS_ADMIN_PASSWORD` from the process environment. It does
not load these credentials from source, generate defaults, print the password,
reset existing users, or mark email verified. A repeat preserves all credentials.
It refuses a conflicting existing account or a second administrator.

Use a local terminal with transcription disabled. These prompts keep the password
out of command history and terminal output (or inject the variables using your
secret manager):

```powershell
$env:SAPS_ADMIN_USERNAME = Read-Host 'Administrator username'
$env:SAPS_ADMIN_EMAIL = Read-Host 'Administrator email'
$sapsAdminSecret = Read-Host 'Strong administrator password' -AsSecureString
$sapsAdminPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sapsAdminSecret)
try {
    $env:SAPS_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($sapsAdminPointer)
    .\.venv\Scripts\python.exe -m app.modules.administration.bootstrap
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($sapsAdminPointer)
    Remove-Item Env:SAPS_ADMIN_PASSWORD, Env:SAPS_ADMIN_USERNAME, Env:SAPS_ADMIN_EMAIL
    $sapsAdminSecret.Dispose()
}
```

Sign in at `/officer/`, then verify the code sent to the configured email inbox.
The command never sends passwords by email. SMTP must already work for sign-in.

## Staff accounts

The dashboard searches all account summaries by username/email, role, station and
active status, with pagination. Only single-role Charge Officer, Station Commander
and Investigating Officer accounts are editable. Complainants, administrators,
other roles and combined-role accounts are read only.

Provisioning takes a unique username, email and service number, a strong initial
password entered through a password field, one staff role, and an active station.
Agree that initial password with the staff member over a secure channel; it is
hashed using the existing password service and never returned or emailed. Staff
must complete the ordinary email-code flow at first and subsequent sign-in.

The administrator can update phone, rank, service number, station, role and active
state. Username/email/password changes and account recovery are deliberately not
provided here, so verified-email ownership cannot be changed by this UI. Existing
user, officer and history IDs are retained. Updates use an expected timestamp to
reject stale edits and revoke all existing sessions, including pending challenges.
Inactive users cannot authenticate. Reassign an investigator's open dockets using
the commander workflow before changing their station/role; deactivation is still
allowed for immediate access removal. Assignment and admin changes share an officer
lock. No automatic case transfer, deletion or historical rewrite occurs.

## Audit

Audit search supports date/time range, actor UUID, exact action, record type and
record UUID, with pagination and entry detail. Actor, action, record and timestamp
are always shown. Account-management role/station/status/rank/service changes are
shown; arbitrary old/new values, private case text, secrets and free-form metadata
are withheld. Audit searches and entry reads themselves append audit events.
No audit update/delete API exists; database append-only privileges/triggers remain
in force. Bootstrap, provisioning and changes also append transactional audit rows.
