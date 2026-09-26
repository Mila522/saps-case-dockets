# KZN receiving stations and complaint categories

Run from the repository root with the saved local development database settings:

```powershell
.\.venv\Scripts\python.exe -m app.modules.stations.seed_kzn
```

This explicit command is restricted to `ENVIRONMENT=development`. It uses the
existing Station model and configured application database/session. It does not
create tables or run at startup. No migration is needed for these changes.

| Station | Physical address | City | Internal project code |
| --- | --- | --- | --- |
| Point | 165 Prince Street | Durban | PROJECT-KZN-POINT |
| Durban North | 4 Norrie Avenue, Durban North | Durban | PROJECT-KZN-DURBAN-NORTH |
| Alexandra Road | 101 Alexandra Road | Pietermaritzburg | PROJECT-KZN-ALEXANDRA-ROAD |

All three are active project receiving stations in KwaZulu-Natal. These codes
are stable **internal project codes**, not official SAPS identifiers. No phone,
coordinates, postal code or other unknown details are invented.

The records use the user-supplied addresses and source links:
[Point](https://www.saps.gov.za/contacts/stationdetails.php?sid=621),
[Durban North](https://www.saps.gov.za/contacts/stationdetails.php?sid=1232&sname=Durban+North),
[Alexandra Road](https://www.saps.gov.za/contacts/stationdetails.php?sid=653).
Direct retrieval of these pages timed out/returned gateway errors during this task.
The search-index copy of the official Durban North page corroborated its address;
official SAPS PAIA documents also list [Point](https://www.saps.gov.za/resource_centre/acts/downloads/paia/english/part1.pdf)
and [Alexandra Road](https://www.saps.gov.za/resource_centre/acts/downloads/paia/zulu/zcont.pdf).
This is not a claim that live contact details or operational status were independently verified.

## Repeatability and preservation

The seed takes a transaction-scoped station table lock so simultaneous seed runs
cannot insert competing records. It matches an existing internal code or normalized
station name in KwaZulu-Natal/KZN, including simple SAPS/police-station name variants.
An existing record retains its UUID, code, name and unrelated data; the supplied
address/city/province are set and it is activated. Foreign keys, reference counters,
complaints and officer assignments are untouched. Multiple matches or a code
collision cause the entire transaction to fail for manual review, without deleting
or merging any records. Only missing stations are inserted.

The command was run twice against the local development database: three records
were created, then all three were reused with identical UUIDs/codes. See the test
results below for repeatability and link-preservation coverage.

## Form and API behavior

`GET /api/v1/complaints/receiving-stations` remains authenticated and requires
`complaint.submit`. It returns active database records only, with UUID, name,
province, city and two address lines. The portal displays name/city/province in
the dropdown and the selected station address beneath it. It begins with
**Select a receiving station** and never fills any incident-location fields.
Inactive/nonexistent IDs remain 404; missing/invalid IDs are 422 on submission.

`app/frontend/crime-categories.json` is the single category definition loaded by
both the browser module and Python request validator. Both the complainant form
and officer walk-in form require **Select a crime category** to be replaced with
a choice. Choosing Other reveals a labelled required description. Switching away
hides/disables/clears that field; its previous value is never included in a request.

New Other categories are saved in the existing `crime_category` column as
`Other: <description>`. The trimmed description must contain 1–143 Unicode
characters; the complete stored value fits the existing 150-character limit.
Unknown new free-text categories and bare Other are rejected. Existing historical
categories are not rewritten or validated on read and remain displayable.
Validation failures retain the form and its values. Existing email verification,
permissions, officer-derived station authority and other case workflows remain.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
node --test tests/frontend/*.test.mjs
.\.venv\Scripts\python.exe scripts/check_complaint_browser.py
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

The browser check uses real local HTTP and Edge with a fake mail adapter and
rollback-only fixture accounts/complaints. It does not send email or retain test
case records. Automated tests also check every predefined category, Other bounds,
historical categories, authenticated station details, invalid station selections,
and repeated seeding while preserving existing IDs/codes/complaint/staff links.

## Files for this change

- Station data/seed: `app/modules/stations/seed_kzn.py`.
- A request validation/response: `app/modules/complaints/categories.py`, `schemas.py`.
- Shared A/B form definitions: `app/frontend/crime-categories.json`, `complaint-fields.mjs`.
- A form/display: `app/frontend/app.mjs`, `style.css`.
- B intake: `app/frontend/case-materials.mjs`.
- Tests: `tests/test_kzn_stations_categories.py`, `test_complaint_registration.py`,
  `tests/frontend/complaint-form.test.mjs`, `complaint-categories.test.mjs`,
  `scripts/check_complaint_browser.py`.

All pre-existing uncommitted email-authentication, investigator and collaborator
work remains in place. No branch merge or push is part of this task.

## Completed checks

- Local seed command run twice: created three, then reused the same three IDs/codes.
  A subsequent database query confirmed exactly one active record for each station.
- Full configured PostgreSQL suite: **236 passed**, two existing dependency
  deprecation warnings, 116.03 seconds.
- Frontend suite: **16 passed**; JavaScript syntax checks passed.
- Real headless Edge complaint check: **passed**, including email login, station
  locality/address display, explicit selection, independent incident details,
  Other switching/validation, retained fields after a real API rejection, saved
  station/category/details and the shared officer category control. Fake mail only.
  The harness now waits for enabled controls and handles delayed Edge shutdown.
- Alembic check: no new upgrade operations; no migration required for this task.
- `git diff --check`: passed (only the existing Git LF/CRLF notice).

No implementation or database execution is blocked. Direct live retrieval of the
three supplied station pages was unavailable, as noted in the source section.
