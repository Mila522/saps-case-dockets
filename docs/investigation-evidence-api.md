# Investigation/evidence verification and demo

This follow-up verifies the merged A/B/C backend at baseline `cb89992`.
There is no frontend stack in this repository. Use the existing `/docs` interface
and the reproducible rollback-only API demonstration test; no UI framework was added.

## Changes to existing request contracts

- Status changes now require `expected_status`, the value last read from the
  assigned docket. A stale value returns 409, with no new history or audit row.
- Custody changes now require `expected_custody_event_id`, copied from
  `custody_version` in GET `/api/v1/evidence/{id}`. A successful change returns a
  custody event whose `id` becomes that version. Refresh before retrying a 409.
- ACCESSED events from file downloads do not change `custody_version`.
- Client-supplied source officer/location fields are rejected (422). Actual source
  values come from locked database state. The expected event ID binds the request
  to that observed source, including location and previous custody changes.
- Locking queries refresh previously loaded ORM objects to avoid stale in-memory
  state after waiting on a competing writer.

Both preconditions are required, including for clients using the earlier C API.
No database migration is needed. Concurrent requests based on the same source
have one winner; the loser receives 409. This does not provide HTTP idempotency
or an external integration event queue.

## Routes and authorization

All paths have prefix `/api/v1`. Authentication is the existing bearer access JWT,
with live session, active user and MFA checks. Officers must also have an active
profile at an active station. Lists use limit 1–100 (default 50), offset >= 0.

| Method | Path | Permission and additional scope |
| --- | --- | --- |
| GET | `/investigations/dockets` | `docket.view_assigned`; active assigned investigator |
| GET | `/investigations/dockets/{id}` | Same, same station |
| GET | `/dockets/{id}` | Existing B `docket.approve`; same-station commander |
| GET, POST | `/dockets/{id}/assignments` | `docket.assign`; same-station commander |
| POST | `/dockets/{id}/assignments/end` | Same; reason required |
| GET | `/dockets/{id}/notes` | `docket.view_assigned`; assigned investigator |
| POST | `/dockets/{id}/notes` | `case.add_note`; assigned investigator |
| POST | `/dockets/{id}/status` | `case.update_status`; assigned investigator; closure also needs `case.close` |
| GET, POST | `/dockets/{id}/evidence` | `evidence.manage`; assigned investigator |
| GET | `/evidence/{id}` | Same |
| GET, POST | `/evidence/{id}/files` | Same; POST uses existing protected upload |
| GET | `/evidence/{id}/files/{file_id}/download` | Same; verified attachment, audited |
| GET | `/evidence/{id}/custody-events` | `evidence.view_custody`; assigned investigator |
| POST | `/evidence/{id}/custody-events` | `evidence.manage`; assigned investigator |

There is no implicit supervisor or national bypass. Existing commanders lack
investigation-write permissions; station supervision does not itself authorize
notes or evidence access. A future national route would require explicit
national-level authorization. Role grants such as management never bypass C's
assignment checks. Complainants cannot read investigation notes or custody.

Sensitive reads and writes create identifier-only audit events. The implementation
uses `InvestigationService.audit` and the protected audit model; this repository
does not have a separate universal audit-service module. Domain services keep
audit writes in their transaction. Nothing copies raw narratives into audit JSON.

## Status and evidence lifecycle

Approval and assignment remain unchanged: B approves, and the first assignment
activates the docket. C allows ACTIVE -> ON_HOLD, ON_HOLD -> ACTIVE, and either
-> CLOSED. Every request requires a nonblank `reason`; closing persists it as
`closure_reason` along with `closed_at`. ARCHIVED is deliberately unsupported by
this investigator route (422); reopening and archival authority remain separate
policy work. Invalid transitions or stale state return 409.

Registration allocates `EVD-{STATION_CODE}-{YYYY}-{NNNNNN}` using counter type
EVIDENCE and Johannesburg calendar year in the same transaction. This is an
internal prototype format, not an official national SAPS format. Registration
creates the REGISTERED event with matching custodian and location; current state
is IN_CUSTODY. File upload calculates size/hash and safely increments version;
PostgreSQL stores metadata only. The already implemented private local storage
is retained; no cloud integration was added.

IN_CUSTODY/TRANSFERRED may transfer, start analysis, release or dispose.
UNDER_ANALYSIS may complete analysis and return to IN_CUSTODY. RELEASED/DISPOSED
are terminal. Transfers require a different active same-station investigator and
a nonblank location/reason. Custody updates change current state and append the
event atomically. File versions, notes, custody and status history remain immutable.

## Reproducible A-to-B-to-C test/demo

Run from the repository root against the migrated development database:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_end_to_end_case_workflow.py
```

The test registers accounts through real authentication/TOTP routes, provisions
officer roles/profiles as isolated fixtures, then switches workflow operations
to `saps_api`. It submits a complaint through A's API instead of directly inserting
it; follows B's review/decision/approval routes; then exercises C. All records
roll back through the established outer transaction. Files use a temporary
directory. The demo creates no permanent accounts and prints no credentials.

For a manual Swagger demonstration, use previously provisioned test identities
and enter credentials only into the running application, not documentation:

1. Register/login as the complainant, complete TOTP, and use Swagger Authorize.
2. POST `/complaints` with station, crime category and incident information. Copy
   `id` and the CMP reference. GET `/{id}/tracking` as that complainant.
3. Authenticate as the station charge officer. POST `/complaints/{id}/review`.
4. POST `/complaints/{id}/decisions` with `{"decision":"ACCEPTED"}`.
5. POST `/complaints/{id}/dockets`. Copy the docket ID and CAS number.
6. Authenticate as station commander; POST `/dockets/{id}/approvals` with
   `{"decision":"APPROVED"}`; POST `/dockets/{id}/assignments` with the
   investigator officer ID and a reason.
7. Authenticate as that investigator. GET `/investigations/dockets` and its
   `/{id}` detail, then POST a note using the Swagger example.
8. POST `/dockets/{id}/status`:

```json
{"expected_status":"ACTIVE","status":"ON_HOLD","reason":"Await laboratory analysis"}
```

9. POST `/dockets/{id}/evidence` using its example. Copy evidence ID, EVD reference
   and `custody_version`. POST `/evidence/{id}/files` with a small local sample;
   inspect returned size, version and SHA-256, then download it through the API.
10. POST `/evidence/{id}/custody-events` using TRANSFERRED, the copied version as
    `expected_custody_event_id`, a same-station destination investigator ID,
    `to_location` and `notes`. GET evidence and history to verify the source and
    destination. Repeating the old request should return 409.

Manual Swagger writes persist normally. Use the automated test for a demonstration
that retains no data. Role provisioning remains with the lead/shared package.

## Small A/B integration correction

The merged baseline had no POST `/api/v1/complaints/{id}/review` route (404), so
the requested explicit review step could not run. A narrowly scoped operation
was added to B's existing refusal router/service: same-station charge officer,
`complaint.decide` permission, SUBMITTED -> UNDER_REVIEW, review timestamp and
`complaint.review` audit in one transaction. Repeat/decided-state requests return
409; foreign-station access returns 404. Existing acceptance/refusal, complaint
registration, approval and assignment implementations were not redesigned.

## Verification and test isolation

```powershell
.\.venv\Scripts\python.exe -B -m alembic current
.\.venv\Scripts\python.exe -B -m alembic heads
.\.venv\Scripts\python.exe -B -m alembic check
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

`test_investigation_contracts.py` extends authorization, validation, immutable
metadata, station counters and OpenAPI checks. Existing database tests cover
invalid SHA-256, zero/negative sizes and duplicate file versions.

`test_investigation_concurrency.py` uses two independently connected PostgreSQL
sessions and waits until both genuinely contend for the docket lock. It creates
a uniquely named disposable `test_c_race_*` database, bootstraps the documented
schema/default-grant prerequisites, and applies the existing migrations there.
Fixtures can commit in this isolated database so both sessions see them. The
database is dropped in teardown after verifying its identity, including after
test failures. The development database is never reused for committed race data.
This test requires the configured migration account's CREATEDB capability;
it does not grant additional privileges. A hard process kill may require manual
cleanup of the precisely named scratch database. No create_all is used.

## Future D integration contract and limits

C closure commits docket status/history, reason, actor and audit together. It
does not send feedback or notifications and does not generate documents or alerts.
D can use docket ID, closing actor/time/reason and the matching history/audit
record as integration inputs, subject to its own authorization/redaction policy.
No delivery guarantee is implied: the integrator must agree an idempotent durable
event/outbox mechanism before promising automatic closure communication. C does
not call D synchronously, so D being unfinished does not block this workflow.

No D implementation, frontend framework, global configuration, shared router,
applied migration or model was changed. Remaining deployment work includes
private storage ACLs/backups, orphan reconciliation, malware scanning, load
testing and independently authorized supervisor/national access policies.
