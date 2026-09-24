# Collaborator D implementation contract

> Historical preparation notes. The current implementation, scope, defaults,
> endpoints, and verification limits are documented in
> [Collaborator D APIs](collaborator-d-api.md). Statements below describe the
> earlier snapshot and are not the current integration status.

Status: initial code inspection complete; live database verification pending.
Source: supplied ZIP snapshot, without Git history. This is not proof of the team's committed baseline.

## Baseline

Six existing revisions form one chain ending at 089c746ed6a9. Never edit them.
The application currently registers only authentication routes.
Use the existing case_mgmt models, authentication dependencies and service-owned transactions.
Do not use create_all or invent a replacement shared baseline tag.

## First feature: docket feedback

Proposed routes:
- POST /api/v1/dockets/{docket_id}/feedback
- GET /api/v1/dockets/{docket_id}/feedback
- GET /api/v1/dockets/{docket_id}/feedback/{feedback_id}

Publishing requires feedback.provide, an active officer and current assignment to the docket. Derive author and recipient from authenticated user and complaint; never accept arbitrary author or recipient IDs.
Reading requires case.track_own plus complaint ownership, or docket.view_assigned plus current assignment. Additional management access must be explicitly agreed before granting it.
Apply scope before returning records; use bounded pagination with stable ordering.
Create feedback and its audit record in one transaction. Audit sensitive reads without copying message content into audit metadata. Roll back on audit failure.
Corrections append a record with supersedes_feedback_id; validate the target belongs to the same docket and recipient. Do not update or delete historical feedback.

The existing model requires a docket. Registration/refusal communications before docket creation must use complaint-linked notifications/documents, not a fabricated docket.
Acknowledgement is deferred: acknowledged_at cannot be updated under append-only protection. A separate acknowledgement table is a proposal for lead migration coordination.

Acceptance tests: valid assigned author; missing permission; unrelated/previously assigned officer; unrelated complainant; inactive officer; cross-docket correction; invalid/blank payload; bounded pagination; audit failure rollback; history preservation.

## Following increments

1. Recipient-scoped in-app notification reads; internal enqueue service.
2. Delivery worker and attempt recording; approved provider, retry and deduplication design.
3. Official confirmation generation and authorised download; shared DOCUMENT counter and private storage.
4. Alert listing/transitions and evaluator; agreed inactivity/deadline rules and repeat-evaluation deduplication.
5. Scoped dashboard queries; agreed metric definitions.
6. Integration with A/B/C events, end-to-end checks and handover.

## Decisions still needed

- Feedback acknowledgement storage and migration owner.
- Notification channels/provider; encryption service for destinations; read/unread requirement.
- Event identities and atomic enqueue contract with A/B/C.
- Document templates, storage location and identifier format.
- Alert thresholds, transition permissions and assignment policy.
- Dashboard definitions and station/central visibility.

## Verification status

Python environment created locally; dependency installation initiated.
No .env was supplied. No live database credentials have been invented.
Existing PostgreSQL tests, alembic current and alembic check remain pending local configuration.
No feature APIs or schema changes have been applied in this preparation increment.

## Feedback API - first implementation

Routes registered under /api/v1:
- POST /dockets/{docket_id}/feedback
- GET /dockets/{docket_id}/feedback?limit=50&offset=0
- GET /dockets/{docket_id}/feedback/{feedback_id}

POST body: feedback_type, subject, message, optional supersedes_feedback_id.
Author and recipient are derived server-side. Publishing requires feedback.provide, an active officer in the complaint station and current docket assignment. Reading requires ownership with case.track_own or current same-station assignment with docket.view_assigned. Management access is not inferred from administrative roles.

Feedback and redacted audit records commit together. Corrections append; there are no update/delete/acknowledgement routes. Notification enqueue is a later increment. Publishing locks docket, complaint, officer and assignment rows; shared workflow owners must coordinate lock order before integration. Acknowledgement, notifications and full end-to-end integration are not complete.

Run credential-free unit checks:
.\.venv\Scripts\python.exe -B -m pytest tests/test_feedback_service.py -p no:cacheprovider --assert=plain -q

These use a mocked repository/session. They do not establish PostgreSQL query correctness, rollback durability or concurrency safety. Live database and HTTP integration checks remain required before merge.

## Integration into VS Code checkout

The supplied baseline and feedback API were integrated into C:/Users/User/saps-case-dockets.
The local .env was preserved. Authentication also requires JWT_SECRET_KEY and MFA_ENCRYPTION_KEY; these are absent from the local configuration as inspected. Configure locally generated secrets before running the application. Never use the temporary HTTP-test settings for normal operation.

Credential-free verification now includes tests/test_feedback_http.py, which isolates settings in a subprocess and exercises real FastAPI routing with mocked database/repository dependencies. PostgreSQL persistence, real permission queries, rollback durability and concurrent corrections remain unverified until a migrated development/test database is available.
