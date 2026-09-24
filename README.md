# saps-case-dockets
An academic prototype exploring requirements and system design for improving the registration, tracking, accountability, and digital management of SAPS case dockets.

Collaborator D adds scoped feedback, transactional in-app notifications, private
printable confirmations, operational alerts, and dashboard APIs. See
[Collaborator D APIs](docs/collaborator-d-api.md) for routes, authorization,
configuration, default policies, and successful full-suite verification.
It reuses the existing schema without adding or modifying migrations.

Authentication is now available under `/api/v1/auth`: complainant registration,
password login, mandatory TOTP enrollment/verification, rotating refresh tokens,
logout, and current-user/RBAC dependencies. See [Authentication](docs/authentication.md)
for request flows, secret configuration, security decisions, deployment requirements,
and testing. Run `.\.venv\Scripts\python.exe -m pytest` for the full suite.
Revision `089c746ed6a9` adds authentication sessions, encrypted MFA methods, and
case-insensitive user indexes. The current schema has 32 application tables.

The completed database foundation is documented in [Database design](docs/database-design.md),
including all 30 tables, RBAC grants, identifier formats, protections, review findings,
and future service rules. Revision `1edbbd1f3330` adds 38 role-permission mappings and
station/year identifier counters. The full migration chain was verified in a newly
created disposable database, including downgrade/replay and preservation of existing
grants; the temporary database was then dropped.

Complaint decisions, refusal escalation tracking, shared-counter CAS allocation,
docket creation, and commander approval APIs are documented in
[Decisions and dockets](docs/decisions-dockets.md). They use the existing schema and
do not add or modify a migration.

Collaborator C adds investigator assignments/reassignment, assigned-docket access,
immutable notes, investigation status changes, evidence registration and custody,
and protected file upload/download. See
[Investigation and evidence APIs](docs/investigation-evidence.md) for routes,
permissions, private storage configuration, and testing. This package reuses the
existing schema and A/B workflows; no migration is required.

## Complaint and docket database

Revision `53b49290f8f4` extends the identity foundation with nine `case_mgmt`
tables: `complaints`, `complaint_statements`, `witnesses`, `witness_statements`,
`refusal_reasons`, `complaint_decisions`, `refusal_escalations`, `dockets`, and
`docket_approvals`. It seeds seven configurable refusal reasons with stable UUIDs.

Complaint references and CAS numbers are separate required, unique strings.
A complaint may exist without a docket. A docket requires an accepted decision
for that same complaint and a complaint status of `ACCEPTED` or `DOCKET_CREATED`.
Composite foreign keys prevent mismatched complaint/decision references.
The migration includes integrity triggers for docket acceptance, protecting the
status of complaints with dockets, and ensuring escalations reference refusals.
These functions are managed explicitly by the migration; Alembic autogeneration
does not compare them. There are no audit-log tables or audit triggers.

Decision and approval history permits only insert/select through `saps_api`.
Statement content is preserved: a correction clears `is_current` on the previous
version and inserts a new version in one transaction. Only `is_current`,
`signed_at`, and `updated_at` may be updated on statements by `saps_api`.
Unique partial indexes allow at most one current statement per complaint/witness.
The migration owner retains maintenance access to these records.

Future services must generate human-readable references and CAS numbers, encrypt
identity values before storage, authorize commander decisions, validate active
refusal reasons and required notes, and atomically coordinate decisions, complaint
status, docket creation, and required escalation records. The schema stores
escalation policy and tracking information; it does not dispatch escalations.

Run migrations and PostgreSQL integration checks from the repository root:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use the configured database, roll back all fixture writes, and verify the
HTTP `/health/database` endpoint using a temporary local server. Downgrading this
revision drops its nine tables and their data; it preserves foundational tables.

## Investigation and evidence database

Revision `fd1a582b8271` adds `case_assignments`, `docket_status_history`,
`investigation_notes`, `evidence_items`, `evidence_files`, and
`evidence_custody_events` in `case_mgmt`. It does not change existing table
definitions or seed data.

Assignments retain their own officer, assigning officer, start/end timestamps,
and reasons. A partial unique index permits one active assignment per docket;
reassignment must close the previous assignment before inserting its replacement
in the same transaction. The docket's existing `status` remains its current state.
History accepts exactly the same six statuses, rejects unchanged transitions, and
allows one initial entry with a null `from_status`. A validation trigger serializes
history validation through the parent docket and prevents inserting that initial
entry after another entry or placing an entry before the initial timestamp.
Alembic does not automatically compare this function or trigger.

Evidence file records contain metadata only, with positive version/size checks,
64-character hexadecimal SHA-256 hashes, unique object keys, and unique versions
per evidence item. File size uses `BigInteger`. Storage keys must be opaque object
identifiers rather than URL or absolute-path references. The database cannot
verify an object's existence, access policy, or actual content hash; those checks
belong to the future storage service. Hash normalization to lowercase is also a
future service responsibility.

Status history, investigation notes, file metadata, and custody events have no
`updated_at`. Applications must treat these as append-only, with database-level
immutability deferred to the audit phase. This revision adds no append-only
triggers or permission revocations. Assignment role checks, coordinated current
status/custodian updates and history writes, and transfer-destination validation
remain service responsibilities. There are no upload endpoints or storage services.

The integration suite covers reassignment history, initial status-history rules,
file constraints (including sizes above 32-bit range), relationship loading,
schema inspection, existing seeds, application access, and HTTP database health.
Downgrading this revision drops only its six new tables and validation function.

## Communications, alerts, and audit database

Revision `a59515a63a4d` adds six tables: `case_feedback`, `official_documents`,
`notifications`, `notification_attempts`, `alerts`, and `audit_logs`. The models
include recipient exclusivity, feedback supersession, protected document keys,
document sizes and hashes, delivery-attempt numbering, alert attribution, and
audit actor checks. Audit payloads and provider metadata use PostgreSQL JSONB;
audit IP addresses use INET. No dashboard tables duplicate the underlying data.

The `case_mgmt.reject_append_only_mutation()` function and ten `aa_append_only`
row triggers reject UPDATE and DELETE on complaint decisions, docket approvals,
docket status history, investigation notes, evidence files, custody events,
feedback, official documents, notification attempts, and audit logs. The runtime
role retains SELECT and INSERT, with UPDATE, DELETE, and TRUNCATE revoked on those
tables. Notifications, alerts, complaints, dockets, escalations, and assignments
retain their existing update behavior. Administrative owners can still change
privileges or disable triggers; this is protection against ordinary application
mutations, not against a database administrator.

Downgrade removes all ten triggers before their shared function and restores
UPDATE/DELETE only on the four existing tables that had those privileges before
this revision. It preserves the earlier restrictions on complaint decisions and
docket approvals. No applied migration was rewritten.

Feedback is immutable in its entirety, including `acknowledged_at`. Under the
requested rules, that value can only be supplied at insertion; a future phase
must define separate acknowledgment records or explicitly revise this policy if
acknowledgment after publication is required. Corrections are new feedback rows
referencing the previous record.

Audit events are not populated automatically. A future audit service must sanitize
JSON payloads and explicitly record sensitive reads/downloads because ordinary
row triggers do not capture SELECT. Destination encryption, provider-response
sanitization, cross-record business validation, document generation, delivery,
object-storage access, and alert scheduling also remain service responsibilities.
The database stores protected-object identifiers but cannot verify the protection
or content of an external object.

The 22-test PostgreSQL suite verifies schema constraints, application privileges,
both UPDATE and DELETE rejection on real rows in all ten tables (including via
the migration-owner connection with triggers enabled), retained mutable-table
updates, relationships, existing seeds, and `/health/database`. Every fixture
transaction is rolled back, including owner-level tests.
