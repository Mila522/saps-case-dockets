# A–C merged workflow review

## Source and Git limits

The requested source is `SAPS_Case_Docket_Management_Final_3-1 (1).pdf`. It was not
present in the workspace, supplied attachment directory, or the named local user
document/download locations searched. Its path was requested. **The PDF has not
been read: this is a code review against the six explicit corrections in the user
request and the existing schema, not a claim of complete PDF conformance.** Exact
PDF clauses, page references, compulsory statement/signature fields and notification
timing still require checking against that file.

Work remains on `feature/investigation-evidence-c`, starting at `a98aec1`, whose
ancestry contains A, B and D. All existing uncommitted investigator work was kept.
No reset, branch change, merge, commit or push is part of these corrections.

## Corrections and API contracts

1. Removed the second review route/service from `refusals`. The authoritative
   `POST /api/v1/complaints/{id}/review` uses `StationComplaintService.start_review`,
   `StationComplaintOut`, and `complaint.review.start`. Tests compare actual included
   routes, OpenAPI and the emitted audit rather than trusting OpenAPI to reveal duplicates.
2. Added `POST /api/v1/complaints/in-station`. Only a live charge officer with
   `complaint.register` at an active station can use it. Officer/station are derived
   server-side. It creates a new unlinked complainant, `IN_STATION` complaint, unique
   reference, server timestamp, responsible officer and initial statement atomically.
   Arbitrary `complainant_id`, `user_id`, officer and station identifiers are forbidden.
   It does not match or take over an online profile by email/phone. The existing
   online route still derives its linked owner and validates the receiving station.
3. Acceptance locks the complaint and creates its decision, docket, CAS counter,
   initial status history, audit and existing D notification records in one transaction.
   No inner commit occurs. The response includes `docket_id`, `cas_number` and
   `docket_status`; complaint status is `DOCKET_CREATED`. The deprecated separate
   docket endpoint is an idempotent 200 “ensure” operation, also supporting legacy
   accepted complaints without dockets. B's UI shows the allocated CAS immediately.
4. Added staff dossier routes under `/api/v1/complaints/{id}`:
   - `GET /materials`: authorised statements, witnesses, append capability and
     initial evidence while awaiting approval; no identity hashes/ciphertexts.
   - `POST /statements`: append a statement version using `expected_version`.
   - `POST /witnesses`: record an actual witness, optionally with their statement.
   - `POST /witnesses/{witness_id}/statements`: append a scoped witness statement version.
   - `POST /initial-evidence`: charge-officer registration after acceptance and before
     approval, reusing C's evidence number, initial custody and audit implementation.
   Charge officers may amend intake until approval; commanders read their station's
   dossier; only the currently assigned investigator may append afterward. Closed
   cases remain read-only. All reads/writes are audited and scoped. There are no
   edit/delete routes for recorded statements. Runtime column privileges still
   prohibit rewriting statement bodies. `signed_at` is never fabricated.
5. Authenticated tracking by UUID/list includes the CAS, without exposing statements,
   notes, evidence or other complainants. `POST /complaints/track-by-reference` accepts
   an exact reference in its body and applies the same live MFA session, permission
   and ownership checks. Missing and foreign references have identical 404 responses.
6. Existing refusal/escalation, commander approval/return, assignment, status, evidence,
   custody, protected file and MFA checks remain. Initial material uses the existing
   tables. No model, migration, database privilege or D backend changes were made.

## Required and later fields

These are the implemented requirements from the existing schema and explicit task;
**PDF-specific mandatory fields remain unverified until the PDF is supplied.**

| Stage | Required | Optional / may follow later |
| --- | --- | --- |
| Online intake | Linked authenticated complainant; active receiving station; crime category; incident description/first statement; incident location and province | City and timezone-aware past incident date |
| Walk-in intake | Active charge officer/station; new complainant first/last name and phone; confirmation of details with the complainant; same incident fields | Email; city/date; genuine witness records |
| Witness record | First and last name | Phone, email, address, statement. Zero witnesses is valid |
| Statement correction | Nonblank text and expected current version | Signature is not assumed or backdated |
| Initial evidence, after acceptance | Actual item title, description, supported type, physical/digital flag and secure location | Past collection time/location. Zero items is valid |
| Protected file | Assigned investigator, non-empty binary file | Added later through C's existing protected upload; server calculates hash/size/version |

The original incident description is preserved as the first statement, not copied
from invented text. Legacy records are not backfilled with fabricated material.
Correction forms preserve previous versions and reject stale updates. Initial
evidence begins in the actual registering officer's custody; assignment does not
silently transfer it. Subsequent custody handover is a separate C event.

Walk-in complainants are not automatically linked to online accounts. A future
verified linking/recovery workflow is needed for their self-service tracking;
anonymous reference knowledge is insufficient. Current phone/email capture does
not claim remote identity verification or a legal signature workflow.

## D handoff — backend preserved, UI unfinished

- **Actual SMS with CAS:** `notify_complainant` currently creates generic `IN_APP`
  records/attempts, not SMS. Implement a configured provider, encrypted destination,
  durable transactional delivery intent, idempotency, retries and delivery-result
  auditing. Attach it to the PDF-specified successful approval event (verify exact
  clause; the available commander event is `DocketService.approve` with `APPROVED`).
  Acceptance/CAS allocation is distinct from commander approval. Do not label a
  generic in-app record as an SMS. An unlinked walk-in profile cannot read in-app
  notifications through an online account yet.
- **Automatic alerts:** the backend exposes explicit `POST /alerts/evaluate` and
  policy settings, but no automatic scheduler/worker was found. D must add scheduled,
  scoped, idempotent evaluation and agree thresholds with the PDF; existing 7-day,
  48-hour and 24-hour defaults are not proven PDF requirements.
- **Confirmations:** D already provides on-demand protected HTML confirmation
  generation/list/download. Verify the PDF's triggering events/content, add any
  automatic generation required and expose authorised display/download. Do not
  describe the current request-driven HTML implementation as automatic SMS or a
  finished document UI.
- **Unfinished UI:** complainant notification and document screens and D dashboard/
  alert/feedback screens remain D-owned. This task adds none of them. Preserve
  recipient/station scope and immutable records when completing those screens.

## Changed files by ownership

Changes made for this review (additional to the existing dirty C frontend):

- **A:** `app/modules/complaints/{router.py,schemas.py,service.py,intake.py}`,
  `app/frontend/{app.mjs,case-materials.mjs}`, `docs/complaint-registration.md`,
  `docs/complaint-tracking.md`, `tests/test_complaint_tracking.py`. The shared
  complaints files also contain B's single authoritative review service.
- **B:** `app/modules/refusals/{router.py,schemas.py,service.py}`,
  `app/modules/dockets/{router.py,service.py}`, `frontend/officer/{index.html,assets/app.js}`,
  `docs/decisions-dockets.md`, `docs/officer-ui.md`, `tests/test_decisions_dockets.py`.
- **C / integration:** `app/modules/evidence/service.py`,
  `frontend/investigator/assets/docket-detail.mjs`, `tests/test_investigation_api.py`,
  `tests/test_end_to_end_case_workflow.py`, `tests/test_intake_corrections.py`,
  `tests/test_intake_concurrency.py`, `scripts/check_investigator_browser.py` and this report.
- **D:** no backend or UI changes. Handoff is above.

Existing dirty files such as shared auth-client refresh work, C routes/schemas,
the C static mount, investigator pages, screenshots and original frontend tests
were retained. Do not confuse all changes since HEAD with work introduced by
this review. `.env`, models and migrations are unchanged.

## Verification

The original 189-test suite and migration checks passed before correction. Focused
new tests exercise route uniqueness, station/role restrictions, impersonation,
statement retention/versioning, owner-only CAS/reference tracking, transaction failure
at counter/history/audit/notification stages and legacy compatibility. Real multi-
connection PostgreSQL races verify a single acceptance winner, an acceptance/ensure
race and distinct CAS allocation across concurrent complaints. Race tests use a newly
created, migrated disposable database and never race the application database.

Final verification on 2026-09-24:

- Full configured PostgreSQL Python suite: **203 passed**, two existing dependency
  deprecation warnings, 83.19 seconds. Includes rollback, permissions, immutable
  history, protected files, complete workflow and three new acceptance race tests.
- Frontend client tests: **9 passed**; changed JavaScript modules pass syntax checks.
- Real headless Edge/Uvicorn browser workflow: **passed**, including MFA, C notes,
  status, evidence/upload/custody, session refresh/expiry, assignment denial,
  desktop/mobile layout, B walk-in intake, witness statement, automatic CAS/docket
  and initial evidence. New screenshots were written to the temporary artifact
  directory, preserving the existing uncommitted preview images.
- Alembic current/head: `089c746ed6a9`; `alembic check`: no new upgrade operations.
- `git diff --check`: passed; Git emits only its configured LF/CRLF notices.
- Git remains on `feature/investigation-evidence-c`, ahead of its tracking branch
  by six existing commits, with the preserved and new uncommitted work. No commit,
  merge or push was performed.

No required test service was unavailable. Until the source PDF is supplied, exact
source comparison and the PDF-specific requirements identified above remain unverified.
