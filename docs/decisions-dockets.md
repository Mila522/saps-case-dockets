# Complaint decisions, refusal escalation and dockets

All endpoints require an MFA-authenticated bearer token and the indicated existing
permission. Services own commits and rollbacks, and mutation/sensitive-read audit
records are written in the same transaction.

## Charge-officer workflow

- `GET /api/v1/refusal-reasons` (`complaint.decide`) returns active controlled reasons.
- `POST /api/v1/complaints/{complaint_id}/decisions` (`complaint.decide`) accepts or
  refuses a station-scoped complaint. The caller must be an active charge officer.
- `POST /api/v1/complaints/{complaint_id}/review` has one authoritative route and
  service (`StationComplaintService`), response (`StationComplaintOut`) and audit
  action (`complaint.review.start`).
- `POST /api/v1/complaints/{complaint_id}/dockets` (`complaint.decide`) is deprecated
  compatibility support: return the existing docket with 200, or create one for a
  legacy accepted complaint that has no docket. Repeats never allocate another CAS.

Accepting a complaint records the charge officer's determination that it is a criminal
matter and creates its docket in that same transaction. It allocates
`CAS-{STATION_CODE}-{YYYY}-{NNNNNN}` from the shared counter, records the initial
`PENDING_APPROVAL` history, both audits and existing D notification hooks, and moves
the complaint to `DOCKET_CREATED`. The 201 decision response includes `docket_id`,
`cas_number` and `docket_status`. A locked complaint and atomic counter upsert prevent
duplicate decisions/dockets/CAS numbers under concurrent acceptance or compatibility
requests. Every failure rolls back the decision, counter, docket, history and hooks.

A compliant reason with `requires_escalation` creates a station-commander escalation.
A non-compliant reason creates both station-commander and NCC escalations. Required
notes, active reasons, complaint row locking, and hidden 404 station scoping are
enforced by the service.

## Escalation workflow

- `GET /api/v1/refusal-escalations`
- `POST /api/v1/refusal-escalations/{id}/acknowledge`
- `POST /api/v1/refusal-escalations/{id}/resolve`

These require `refusal.escalation.view`. Station commanders see and mutate only
`STATION_COMMANDER` records for their own station. NCC officers see and mutate only
`NCC` records across stations. Resolution requires non-blank notes.

## Commander docket workflow

- `GET /api/v1/dockets/{id}`
- `POST /api/v1/dockets/{id}/approvals`

These require `docket.approve`, an active officer profile, the `STATION_COMMANDER`
role, and same-station authority. `APPROVED` moves a pending docket to `APPROVED`
and appends status history. `RETURNED_FOR_CORRECTION` requires notes and records an
immutable approval entry while leaving the docket pending for a later review.

No schema change or migration is required. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_decisions_dockets.py -q
```
