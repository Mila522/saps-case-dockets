# Complaint decisions, refusal escalation and dockets

All endpoints require an MFA-authenticated bearer token and the indicated existing
permission. Services own commits and rollbacks, and mutation/sensitive-read audit
records are written in the same transaction.

## Charge-officer workflow

- `GET /api/v1/refusal-reasons` (`complaint.decide`) returns active controlled reasons.
- `POST /api/v1/complaints/{complaint_id}/decisions` (`complaint.decide`) accepts or
  refuses a station-scoped complaint. The caller must be an active charge officer.
- `POST /api/v1/complaints/{complaint_id}/dockets` (`complaint.decide`) creates one
  docket from an accepted decision at the officer's station.

An accepted decision leaves the complaint in `ACCEPTED`. Docket creation separately
allocates `CAS-{STATION_CODE}-{YYYY}-{NNNNNN}` from the shared `identifier_counters`,
creates the initial `PENDING_APPROVAL` status-history row, and changes the complaint
to `DOCKET_CREATED` atomically.

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
