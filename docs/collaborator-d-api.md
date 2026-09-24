# Collaborator D: communications and oversight

This implementation covers feedback, in-app notifications, printable confirmations,
operational alerts, and dashboard APIs. It integrates with the existing A/B/C
services. The existing complainant portal is unchanged; these APIs are available
through `/docs` or an authenticated API client.

No schema migration is required. The six applied revisions are unchanged and the
single Alembic head remains `089c746ed6a9`. All identifiers use the shared counter
service; no tables are created at application startup.

## Feedback

| Method | Route under `/api/v1` | Behavior |
|---|---|---|
| POST | `/dockets/{docket_id}/feedback` | Publish immutable feedback or append a correction |
| GET | `/dockets/{docket_id}/feedback` | List scoped feedback, newest first |
| GET | `/dockets/{docket_id}/feedback/{feedback_id}` | Read a scoped record |

Publishing requires `feedback.provide`, an active officer at an active station,
the complaint's station, and the current docket assignment. Author and recipient
are derived from the authenticated user and complaint. Readers need ownership
with `case.track_own`, or current same-station assignment with
`docket.view_assigned`. Administrative roles do not imply feedback access.

Example request:

```json
{
  "feedback_type": "PROGRESS_UPDATE",
  "subject": "Investigation update",
  "message": "Please contact your investigating officer."
}
```

Corrections add `supersedes_feedback_id`. The target must belong to the same
docket/recipient and must not already have a correction. The docket is locked
before assignment checks, matching C's lock order. There is no update/delete
endpoint. `acknowledged_at` remains untouched because the existing database
protects the entire feedback row as append-only.

## Notifications

`GET /notifications` and `GET /notifications/{id}` return only delivered in-app
messages belonging to the authenticated user or their linked complainant. The
response excludes destinations, provider metadata, and internal recipient IDs.

The following workflow mutations deliver a generic notification, one delivery
attempt, and a redacted audit record in the caller's transaction:

- Complaint registration and acceptance/refusal decisions.
- Docket creation and investigator assignment/reassignment.
- Investigation status changes, including closure.
- Feedback publication and corrections.

Messages direct recipients to sign in; they do not copy private case narratives.
Notification failure rolls back the originating mutation. Workflow locks/state
checks prevent duplicate deliveries for rejected transition retries. Complaint
registration retains A's existing request/retry behavior; this is not a new
cross-request idempotency system.

IN_APP delivery is immediate and transactional, so it requires no background
delivery worker. SMS/email dispatch is not implemented or represented as sent.
Adding it requires an approved provider, destination encryption, delivery
idempotency, and retry policy. Read/unread tracking and feedback acknowledgement
are not added to the existing schema.

## Documents

| Method | Route | Behavior |
|---|---|---|
| POST | `/complaints/{id}/documents` | Generate or retrieve an existing confirmation |
| GET | `/complaints/{id}/documents` | List authorized document metadata |
| GET | `/documents/{id}/download` | Audit and download an integrity-checked file |

POST accepts only `document_type`:

- `COMPLAINT_REGISTRATION_CONFIRMATION`: complaint reference and issue date.
- `REFUSAL_CONFIRMATION`: requires the latest decision to be a refusal; includes
  the configured refusal reason, excluding internal officer notes.
- `DOCKET_REGISTRATION_CONFIRMATION`: requires a docket; includes the CAS number.
- `CASE_CLOSURE_CONFIRMATION`: requires a closed/archived docket with a closure date.

Every route requires `confirmation.download` plus complaint ownership and
own-case read permission, station-scoped complaint access, or current same-station
investigator assignment. Global administration permissions alone grant no access.
Generation is deterministic from existing workflow records; clients cannot submit
document content, authors, storage keys, or document numbers.

The shared DOCUMENT counter supplies `DOC-{station_code}-{year}-{sequence}`.
Generation locks the complaint, so concurrent requests for the same confirmation
return one immutable document and consume one number. No historical files are
overwritten. The current workflow permits one terminal complaint decision and one
case closure; future reopen/redecision workflows would need versioned confirmation
semantics before reuse of this policy.

Files are printable UTF-8 HTML, explicitly labelled as an academic prototype,
with escaped content and no private incident narratives. These are not signed
official SAPS forms or PDF templates. They use opaque private storage keys and
verified SHA-256 hashes/lengths. Downloads have attachment, no-store, nosniff, and
sandbox CSP headers. Do not mount the storage directory as static content.

Files are removed after a known pre-commit failure. A file is retained after an
ambiguous commit failure to avoid destroying a potentially committed historical
document; operations must reconcile orphaned files against stored keys after such
a failure. Back up document files together with the database.

## Alerts

- `GET /alerts?status=OPEN`: list visible alerts.
- `POST /alerts/evaluate`: evaluate the commander's station and return new IDs.
- `PATCH /alerts/{id}`: acknowledge, resolve, or dismiss a station alert.

Reads require `alert.view`, plus station commander scope or an explicitly granted
global dashboard role (`SAPS_MANAGEMENT`, `SYSTEM_ADMINISTRATOR`, `NCC_OFFICER`
with `dashboard.view_all`). Writes require `alert.view`, the
`STATION_COMMANDER` role, `docket.assign`, and an active same-station officer.
`alert.view` alone is never treated as a write grant. An alert assigned to another
user cannot be changed. These are implementation policy defaults built from the
existing grants; narrower alert-management grants would need lead coordination.

Example transition:

```json
{"expected_status": "OPEN", "status": "ACKNOWLEDGED", "notes": "Review started"}
```

Transitions lock and refresh the alert, check the expected status, record the
actor/time and notes, and audit the change in the same transaction. Resolved or
dismissed alerts are terminal. Acknowledgement assigns an unassigned alert to
the acting commander.

Evaluation detects:

| Alert | Definition |
|---|---|
| NON_COMPLIANT_REFUSAL | Refusal decision with a reason flagged non-compliant |
| UNACKNOWLEDGED_ESCALATION | OPEN escalation older than the configured threshold |
| OVERDUE_DOCKET | Docket still PENDING_APPROVAL after the configured threshold |
| CASE_INACTIVITY | APPROVED/ACTIVE/ON_HOLD docket without recent recorded activity |

Activity includes docket timestamps, notes, status history, assignments, evidence
item updates, and published feedback. Evidence-file reads do not count as progress.
The evaluator uses a transaction advisory lock per station and source identifiers
to prevent repeat alerts, including for resolved/dismissed episodes. A later
activity timestamp can create a new inactivity episode. Resolution is explicit;
the evaluator does not silently close alerts after workflow changes. Existing
evidence-custody/system-compliance alert rows remain listable, but no unsupported
detection rules are invented for those types.

Evaluation runs on the explicit authenticated endpoint; no scheduler is started
inside the API process. An operational scheduler can call it using the team's
approved authentication flow. Thresholds are prototype defaults, not statements
of SAPS policy or statutory deadlines.

## Dashboards

`GET /dashboards/summary` returns status counts for complaints, dockets, alerts,
and refusal escalations, plus the number of unassigned open dockets and generation
time. Open means APPROVED, ACTIVE, or ON_HOLD; unassigned means no active
`case_assignments` row. Escalation counts count targets separately. Empty status
groups are omitted. Counts include historical records and have no date filter.

Station commanders with `dashboard.view_station` see only their active station.
The three global roles above require `dashboard.view_all`. An arbitrary user with
a misplaced global permission does not gain access without the required role.
Queries aggregate existing workflow tables; there are no dashboard tables or
copies of private case content. Every dashboard read is audited.

## Configuration and operational boundaries

`.env.example` documents these settings. Never commit `.env` or its backups.

```dotenv
# Default: .document-storage in the project root
# DOCUMENT_STORAGE_PATH=C:/private/saps-documents
ALERT_INACTIVITY_DAYS=7
ALERT_DOCKET_APPROVAL_HOURS=48
ALERT_ESCALATION_HOURS=24
```

List endpoints accept `limit` (1–100, default 50) and nonnegative `offset`.
Feedback returns a page object; notification/document/alert lists return arrays.
All sensitive routes enforce the existing production HTTPS rule and disable
caching, including error responses. Services own commits and rollbacks; routes
only validate input, invoke services, and construct responses.

## Verification

Run isolated service and HTTP checks:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_collaborator_d_services.py tests/test_collaborator_d_http.py tests/test_feedback_service.py tests/test_feedback_http.py -q -p no:cacheprovider
```

Run integration and concurrency tests with a migrated development/test database:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_collaborator_d_integration.py tests/test_collaborator_d_concurrency.py -q -p no:cacheprovider --tb=short
.\venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --tb=short
```

Integration fixtures roll back records and use temporary document storage. Race
tests reuse C's disposable-database fixture and require database-creation rights;
they build the schema through Alembic, never through model metadata.

Latest full-suite rerun outside the sandbox, after the user restored restricted
runtime grants and provisioned `saps_owner` for the disposable database fixtures:
**182 passed, 0 failed, 0 errors**, in 86.38 seconds (exit code 0).

This includes:

- All isolated D/feedback service and HTTP checks.
- Both D database integration tests: the complete workflow/access-scope test and
  the feedback notification audit-failure rollback test.
- Both D concurrency tests: one winning feedback correction and one immutable
  document/number for simultaneous confirmation requests.
- Existing C concurrency checks and the A/B/C/authentication regression suite.
- Runtime history-table restrictions, column-restricted statement updates,
  append-only triggers, and database foundation checks.

The earlier database setup and excessive-grant failures are resolved. Two
non-failing dependency deprecation warnings remain: Starlette's HTTPX test-client
integration and its use of the AnyIO BlockingPortal alias.

The test run did not change role grants, search paths, migrations, `.env`, or test
assertions. Previous read-only Alembic checks reported `089c746ed6a9 (head)` and
no new upgrade operations. The six migration files remain unchanged. The current
regression gate passes; the documented feature scope and operational boundaries
elsewhere in this guide still apply.
