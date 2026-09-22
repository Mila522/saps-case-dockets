# Authentication and authorization

This phase adds only `/api/v1/auth` endpoints. Complaint, docket, evidence, delivery,
and frontend features remain outside scope. Migration `089c746ed6a9` adds
`auth_sessions`, `user_mfa_methods`, and case-insensitive unique indexes on
`lower(users.username)` and `lower(users.email)`. Existing exact-case indexes and
applied migrations are preserved. The migration repeats its conflict check while
holding a write-blocking lock on users; it never merges or deletes accounts.

## Configuration

Copy the non-secret settings from `.env.example`, then generate independent keys
with `secrets.token_urlsafe(48)` for JWT and `Fernet.generate_key()` for MFA encryption.
Write generated values directly to `.env` through a local script or a secret manager;
do not display them, commit them, or put them in shell arguments/history. This work
populated missing local keys without printing them. Runtime startup never generates
new keys or falls back to defaults. Losing the encryption key makes stored TOTP
secrets unusable; key rotation needs a deliberate migration/re-encryption process.

| Setting | Default / requirement |
| --- | --- |
| ENVIRONMENT | development; test and production also supported |
| JWT_SECRET_KEY | Required strong secret, at least 32 bytes; placeholders rejected |
| JWT_ALGORITHM | HS256 only; never selected from the incoming token |
| ACCESS_TOKEN_EXPIRE_MINUTES | 15, positive |
| REFRESH_TOKEN_EXPIRE_DAYS | 7, positive, from each successful issuance/rotation |
| MFA_ENCRYPTION_KEY | Required valid Fernet key; repetitive placeholder keys rejected |
| MAX_FAILED_LOGIN_ATTEMPTS | 5, positive |
| ACCOUNT_LOCK_MINUTES | 15, positive |
| MFA_CHALLENGE_EXPIRE_MINUTES | 5, positive and at most 10 |

Validation runs when application settings load, before startup. Secret fields use
masked Pydantic representations. Database errors hide SQL parameter values and auth
service errors never log request bodies or credentials. Production auth endpoints
reject non-HTTPS requests. When terminating TLS at a proxy, configure Uvicorn to
trust forwarded headers only from the actual proxy, never arbitrary clients.

## Endpoints and flow

All requests are JSON. Token-bearing responses use `Cache-Control: no-store`.

| Method/path under `/api/v1/auth` | Request | Result |
| --- | --- | --- |
| POST /register | username, email, password, first_name, last_name, phone_number, optional preferred_contact_method | 201 with MFA_SETUP_REQUIRED, challenge_token, expires_in |
| POST /login | username (username or email), password | MFA_SETUP_REQUIRED or MFA_REQUIRED plus challenge_token |
| POST /mfa/setup | challenge_token from setup-required response | provisioning_uri, manual_entry_secret, setup_token, expires_in |
| POST /mfa/verify-setup | setup_token, six-character code | access_token, refresh_token, token_type, expires_in |
| POST /mfa/verify | challenge_token from MFA_REQUIRED response, code | access_token, refresh_token, token_type, expires_in |
| POST /refresh | refresh_token | Rotated access/refresh pair; old session revoked |
| POST /logout | refresh_token | 204; revoke the session and its rotation descendants |
| GET /me | Authorization: Bearer access_token | Safe user summary with live roles/permissions and linked complainant/officer IDs |

Registration normalizes usernames/emails to lowercase, creates user + complainant +
COMPLAINANT assignment + audit event in one transaction, and never issues normal
access before MFA verification. Usernames are ASCII letters/digits with underscore,
dot, or hyphen, start with a letter/digit, and cannot contain `@`; this keeps email
and username lookup namespaces unambiguous. Public request schemas reject unknown
fields, including role or privilege flags. Passwords require 12 characters, upper-
and lowercase letters, a number, a special character, and no surrounding whitespace.
The 1,024-character upper bound rejects oversized passwords; none are truncated.
Email syntax is validated with EmailStr. TOTP enrollment does not prove email
ownership, so `is_verified` is not set by enrollment.

For enrollment, scan the provisioning URI with an authenticator using issuer
`SAPS Case-Docket Management`. The manual secret and URI are returned only once
per setup attempt, after the encrypted pending method is committed. If that response
is lost, log in again and begin a fresh setup. A pending method does not count as MFA.
Only a valid code activates it, sets `mfa_enabled`, and permits token issuance.

Login failures, inactive accounts, locked accounts, and unknown accounts return a
generic 401. A dummy Argon2 hash is checked for unknown/ineligible accounts. Failed
password attempts are serialized on the user row and lock the account at the
configured threshold. After the lock expires the password attempt count can reset.
Password success resets failed password attempts but cannot bypass MFA.

MFA failures are counted from sanitized, persisted audit events for the user during
the lock window. Requesting another challenge or successfully verifying a password
does not erase this MFA guessing budget. Codes accept the current 30-second step
and one neighboring step on either side. The last accepted step boundary is stored
in `user_mfa_methods.last_used_at`; an equal or older step is rejected. Immediately
logging in after enrollment may require waiting for the next authenticator code.

## Tokens, sessions, and concurrency

Access JWTs include sub, sid, type=access, iat, exp, jti, issuer, and audience. Decoding
requires these claims, valid UUIDs, the correct purpose, expiration, issuer/audience,
and the configured algorithm. Roles and permissions are never embedded in JWTs.

Temporary MFA tokens have distinct purposes (`mfa_setup`, `mfa_enroll`, `mfa_login`)
and are bound to short-lived auth_sessions rows. Enrollment verification also binds
the exact method ID. Their backing session contains the hash of an independent
random value whose preimage is discarded, so neither the JWT nor its jti can be used
as a refresh token. Setup is returned once and verification consumes the challenge
by revoking its session. A client cannot mint an access JWT for a pending session.

Refresh credentials are cryptographically random 48-byte opaque values. Only SHA-256
hashes are stored. Refresh locks the user and session, creates a replacement, revokes
the old session, links the replacement, and commits with its audit event. Each new
refresh session gets the configured lifetime; there is no absolute device-session
lifetime in this phase. Clients must serialize refresh requests: using a rotated
credential again rejects the request and revokes that rotation chain as a possible
theft signal. Unrelated logins remain valid. Expired credentials cannot refresh or
revoke a newer chain. Logout proves possession of the opaque token, needs no access
JWT, and is idempotent for an unexpired known session.

All service paths use a consistent lock order: user, session, then MFA method where
needed. Consuming codes/challenges and rotating credentials is serialized through
the user lock. Access requests check session expiration/revocation, current user
active/MFA state, and current database permissions. Rotation/logout therefore
invalidates access JWTs tied to the revoked session immediately on the next check.
An account password lock blocks new login/MFA/refresh but does not alone invalidate
already-issued access; deactivation or session revocation does.

## Reusable dependencies

`get_current_session`, `get_current_user`, `get_current_active_user`, `get_user_roles`,
`get_user_permissions`, `require_role`, and `require_permission` live in
`app/modules/authentication/dependencies.py`.

```python
Depends(require_permission("complaint.register"))
```

Missing/invalid/expired/revoked authentication is 401, with a Bearer challenge.
An authenticated user lacking the required capability receives 403. Permission
changes take effect without reissuing JWTs. These global dependencies do not grant
cross-station access: later services must check station or national authority,
complainant ownership, and active investigator assignment against the actual case.

## Audit and failure handling

Events include auth.registration, auth.password_verified, auth.login_failure,
auth.account_lockout, auth.mfa_setup, auth.mfa_verified, auth.mfa_failure,
auth.token_refresh, auth.refresh_reuse, auth.session_rejected, and auth.logout.
Only fixed action names, user/session identifiers, and parsed client IP are written.
No passwords/hashes, JWTs, refresh credentials/hashes, secrets, OTPs, arbitrary
request bodies, or provider metadata are copied to audit records.

Audit writes are in the same transaction as successful mutations. Failure produces
a generic 503 and rollback, never a successful unaudited login. Rejected credentials
deliberately commit failure counts and their audit events before returning 401.
An audit failure on that path also fails closed with rollback and 503. Authentication
logs use a fixed message without exception parameters or credentials. Validation
responses strip Pydantic's input/context fields to avoid echoing secrets.

## Tests and local verification

Run `pytest` from the project root using the virtual environment. API tests override
get_db with a Session using nested savepoints inside an outer rollback transaction;
service commits therefore do not retain test records. Existing database tests remain
rollback-only. Tests cover password/JWT/TOTP primitives, configuration, registration,
case-insensitive conflicts, privileged-field rejection, MFA setup/login/replay,
password/MFA lockout, session expiration, refresh/reuse, logout, live RBAC revocation,
inactive users, audit-failure rollback, no-store responses, HTTPS, and safe summaries.

`uvicorn app.main:app --reload --host 127.0.0.1 --port 8011` was started for real HTTP
checks: /docs, /health/database, and /openapi.json returned 200; unauthenticated
/api/v1/auth/me returned 401. The eight routes were present in OpenAPI. The server
was stopped after verification. Full registration/enrollment/login flows were checked
through TestClient with rollback, avoiding permanent smoke-test accounts.

The installed Starlette emits two upstream test-client deprecation warnings concerning
httpx and AnyIO. They do not fail tests; requested httpx remains installed. Before public
deployment, configure trusted TLS termination, ingress request/body/rate limits,
secret backup/rotation, observability, and retention for pending/expired auth sessions.
There is no MFA recovery/reset, password-reset delivery, email verification, session
management UI, or device-wide logout endpoint in this phase.

## Library references

- [pwdlib guide](https://frankie567.github.io/pwdlib/guide/)
- [PyJWT usage and claim validation](https://pyjwt.readthedocs.io/en/stable/usage.html)
- [PyOTP TOTP and replay/throttling guidance](https://pyauth.github.io/pyotp/)

The next feature phase should implement transactional complaint registration with
identifier allocation, station/ownership checks, and sanitized audit events using
these authentication dependencies.
