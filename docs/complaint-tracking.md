# Complainant tracking

Use the existing registration, MFA and login flow in `authentication.md`. Send the
resulting access token as `Authorization: Bearer <access_token>`.

- `GET /api/v1/complaints/mine?limit=20&offset=0` lists the signed-in complainant's
  complaints, newest first, with a stable ID tie-breaker. Limit is 1–100; offset is
  nonnegative. The response includes `items`, `limit`, `offset` and `has_more`.
- `GET /api/v1/complaints/{complaint_id}/tracking` returns one owned complaint.
  Obtain its UUID from the list response.
- `POST /api/v1/complaints/track-by-reference` accepts `{"reference_number":"..."}`
  under the same authentication and owner checks. Guessed or non-owned references
  return the same 404. There is no anonymous reference lookup.

All endpoints require the existing `case.track_own` permission and a complainant
profile linked to the authenticated user. The client cannot choose an owner.
Global permissions never bypass ownership. Unlinked in-station profiles need a
separate verified account-linking workflow; these endpoints do not claim them.

Each item contains `id`, `reference_number`, `status`, `station_id`, `submitted_at`,
`review_started_at`, `updated_at` and nullable `cas_number` (filled after acceptance).
Tracking reflects the current complaint row;
it does not invent a status history or expose statements, witnesses, evidence or
internal investigation notes. See `complaint-registration.md` to submit a complaint.

Missing/invalid/revoked tokens return 401; missing permission or profile returns
403. Missing and non-owned complaint IDs both return the same 404. Sensitive
responses, including errors, use `Cache-Control: no-store`. Production requires
HTTPS, matching authentication endpoints.

Services commit mandatory `complaint.track_own` audit records for returned rows,
plus `complaint.list_own` for list requests including empty lists. Audits contain
actor and entity IDs, and station ID for complaint reads; no complaint narrative
or credentials. Database/audit failure rolls back and returns 503 without data.

No schema or identifier allocation changes are required. Run
`python -m pytest tests/test_complaint_tracking.py tests/test_authentication.py`
against the configured migrated PostgreSQL test database. Fixtures use savepoints
inside an outer rollback transaction so service commits do not persist test data.
