# Online complaint registration

`POST /api/v1/complaints` requires an MFA-authenticated bearer access token and the
existing `complaint.submit` permission. It registers an online complaint for the
complainant profile linked to the signed-in user.

Example JSON:

```json
{
  "station_id": "00000000-0000-4000-8000-000000000001",
  "crime_category": "Theft",
  "incident_description": "Describe what happened.",
  "incident_location": "Location of the incident",
  "incident_province": "Gauteng",
  "incident_city": "Johannesburg",
  "incident_occurred_at": "2026-09-01T14:00:00+02:00"
}
```

Replace the example station UUID with an existing active station. City and incident
date are optional. Dates must include a timezone and cannot be in the future.
Required text is trimmed and cannot be blank. Narrative length is limited to 20,000
characters; other limits match database fields. Unknown fields are rejected.

The service sets ownership, channel `ONLINE`, status `SUBMITTED`, submission time,
and reference number. Clients cannot choose an officer or submit for another person.
Complainants may select an active receiving station; this does not give them access
to other complaints at that station. In-station officer intake remains separate.

Success returns 201 with the same safe fields as the tracking detail endpoint,
including the complaint UUID and reference. Use that UUID to track the complaint.
Authentication failures return 401, missing permission/profile 403, and missing or
inactive stations 404. Invalid input returns 422.

References use `CMP-{STATION_CODE}-{YYYY}-{NNNNNN}`. Year is selected in
`Africa/Johannesburg`; six digits is a minimum width and larger numbers expand.
Station codes must contain alphanumeric segments separated by single hyphens;
invalid station configuration returns 409. Preserve station codes after issuing
references and never reassign used codes in station administration. Destination
uniqueness remains the collision guard; collisions fail closed without overwriting.

The shared `identifier_counters` row is incremented atomically using PostgreSQL
upsert. Allocation, complaint insertion and `complaint.submit` audit insertion
commit together. Failure rolls them all back and returns 503 without internal
details. Audit records contain actor, complaint and station IDs, never the narrative.
No migration is required. Repeated successful POSTs create separate complaints;
this endpoint does not implement an idempotency-key protocol.

The supplied incident description is now also preserved verbatim as version 1 in
`complaint_statements`; no witness, evidence, signature or additional narrative is
invented. Staff append corrections as new versions rather than overwriting it.

`POST /api/v1/complaints/in-station` provides the separate charge-officer workflow.
It requires `complaint.register`, a live charge-officer profile at an active station,
new complainant details and explicit confirmation with that complainant. It derives
the receiving station and responsible officer; caller-supplied owner/officer/station
IDs are rejected. It creates an **unlinked** walk-in profile, not a claim on an existing
online account. See [A–C review](ac-workflow-review.md) for fields, material routes,
privacy limits and the D handoff.

Run `python -m pytest tests/test_complaint_registration.py tests/test_complaint_tracking.py tests/test_authentication.py`
with the project dependencies and migrated PostgreSQL test database configured.
Integration fixtures roll back their records, including service commits.
