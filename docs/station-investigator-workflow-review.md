# Station and investigator workflow review

The existing branch and uncommitted KZN/category/demo-account work are preserved.
No account, station, migration, role grant, authentication implementation, or D
dashboard is changed by this review. No commit, merge, push, or branch switch.

## Behavior

- Online receiving-station selection remains independent of incident location.
  The existing backend persists that selection and rejects missing, nonexistent,
  and inactive station IDs. Existing tests cover this behavior.
- Station complaint lists remain restricted by the authenticated active officer's
  single assigned station for both charge officers and commanders. Each returned
  complaint now includes `receiving_station` (name, locality and address), displayed
  separately from incident location in the queue and detail dialog. Commanders see
  View actions rather than charge-officer-only review/decision buttons.
- Walk-in intake has a required Receiving station select containing only the active
  assigned station, preselected. It loads through the authenticated, permission-
  checked, audited `GET /api/v1/complaints/in-station/receiving-station` endpoint.
  Submission waits for successful loading. Failed loading offers a close/reopen
  retry without submitting. Selecting this station does not change incident fields.
- Walk-in POST accepts `station_id`, checks it against the authenticated officer,
  and still derives officer identity and effective station server-side. Unknown or
  foreign station IDs return 403; malformed IDs return 422; inactive authority
  returns 403. For older clients, omitted/null station IDs still derive the assigned
  station; the updated UI always requires and submits the selected value. Existing
  complainant creation, reference allocation, statement, audit and notification
  transaction behavior is preserved.
- `/officer/` remains the shared password plus emailed six-digit-code entry point.
  After verification, `/auth/me` roles AND permissions select a fixed workspace.
  Investigators now reach `/investigator/` even when signing in directly at
  `/officer/`. Charge officers/commanders stay in their existing role-scoped UI.
  Browser query parameters cannot choose an arbitrary destination or grant access.
- Investigator statuses were already supplied by `allowed_next_statuses`. The
  status form was inside a collapsed details element near the bottom of the docket
  page. It now starts expanded and has a Change status navigation link. Missing
  assignment/permission or a closed docket still prevents unauthorized controls;
  there is no fallback list of invented statuses. Existing reason validation,
  expected-status conflict refresh, append-only history and evidence rules remain.

## Files changed for this review

- Backend: `app/modules/complaints/{schemas,router,service,intake}.py`.
- Shared walk-in form: `app/frontend/case-materials.mjs`.
- Staff UI: `frontend/officer/assets/app.js`, `workspace.mjs`.
- Investigator UI: `frontend/investigator/assets/docket-detail.mjs`, `ui.mjs`.
- Tests: `tests/test_intake_corrections.py`, `tests/test_officer_work_queue.py`,
  `tests/frontend/staff-workspace.test.mjs`, `scripts/check_investigator_browser.py`.
- This report. Other dirty files predate this review and are retained.

## Verification

Commands:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
node --test tests/frontend/*.test.mjs
$env:SAPS_BROWSER_ARTIFACTS = Join-Path $env:TEMP 'saps-workflow-verification'
.\.venv\Scripts\python.exe scripts/check_investigator_browser.py
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

Read-only demo preflight confirmed `demo_charge_officer`,
`demo_station_commander` and `demo_investigator` exist at Point. Their passwords
and inbox codes are unavailable to this session, so no claim is made of signing
in as those accounts. Browser automation uses separate rollback-only fixture
accounts and fake email delivery; it does not reset or seed over the database,
send actual email, or alter demo credentials/assignments. Its screenshots contain
synthetic case data and go to the temporary artifact directory above.

Focused PostgreSQL tests: **57 passed**. Full configured PostgreSQL suite:
**250 passed**, with two dependency deprecation warnings. This includes existing
transaction/concurrency, ownership, refusal, approval, assignment, protected-file,
custody and immutable-history tests. Frontend tests: **17 passed**. JavaScript
syntax checks and `git diff --check` passed (Git reports LF/CRLF notices only).
Alembic check found no new upgrade operations.

The first browser attempt stopped on a harness timing error: logout was clicked
while the portal was still loading its complaint list and its controls were
disabled. Its Edge cleanup also timed out. The test now waits for enabled controls
and continues database rollback even if Edge cleanup times out. Only that failed
test's identified Python processes were stopped to release its transaction.
The corrected real Edge browser run **passed with exit code 0**:

- Portal registration/login and shared staff password/email-code verification.
- Assigned queue/search/empty state, append-only note duplicate-submit guard,
  evidence registration, protected upload/hash metadata and custody changes.
- Status and custody stale-update refresh, session refresh/expiry/logout,
  revoked assignment and role access, desktop/mobile layout checks.
- Required preselected authorized station in walk-in intake; actual witness
  capture; review/acceptance and automatic CAS/docket; initial evidence.
- Commander email-code login, station-labelled queue with read-only complaint
  actions, docket approval and investigator assignment through the UI.
- Direct `/officer/` investigator login routed to the assigned workspace;
  note and ACTIVE -> ON_HOLD -> ACTIVE -> CLOSED changes, displayed reasons
  in status history, and no status/note/evidence write forms after closure.

The only unverified requested scenario is signing in as the existing local demo
accounts with their real inboxes; their credentials were not available. No
implementation, database-test, migration, or fixture-browser blocker remains.
