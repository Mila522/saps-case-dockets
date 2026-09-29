# Updated role workflows

Run `.venv/Scripts/alembic.exe upgrade head` and restart the app. Revision
`aea1947e1d7f` adds password recovery, complaint uploads and vehicle number plates.
The local database has been upgraded; other deployments need the same migration.

- All roles verify their email on initial registration/first account activation.
  Subsequent verified sign-ins require the password. Legacy authenticator accounts
  retain their protected one-time transition. Complainants can use Forgot password.
- Account registration collects residential address, city and province. Complaint
  intake separately collects incident location, city/province and receiving station.
- Online and walk-in complaint forms accept optional witnesses and private images,
  videos, audio/voice recordings and documents. Vehicle theft requires a number
  plate and a vehicle registration document in both flows, including server checks.
- Complaint details provide registration and, when refused/escalated, refusal
  confirmation downloads. These are printable HTML documents; the browser can
  print them to PDF. Complainants may upload further evidence from this screen.
- Accepting a complaint automatically creates the numbered docket and initial
  status history in the same transaction. Empty dossier sections are ready for
  statements, witnesses, investigation notes, evidence, approvals and assignments.
- Investigators can publish official feedback from their assigned docket. Feedback
  is preserved permanently and appears in the complainant's official updates.
- Station commanders use **View full docket** to inspect the complaint, contact
  details, statements, witnesses, decision/approval history, assignments, notes,
  evidence and custody history, feedback and document metadata. Both complainant
  uploads and investigator evidence files have authorized download controls.
- All portals use the supplied SAPS logo, large SAPS branding, and a home
  page with prominent stakeholder entry points. Staff sign-in has no background badge.

## Private upload contract

`POST /api/v1/complaints/uploads` accepts multipart `file`, `purpose` (EVIDENCE or
VEHICLE_REGISTRATION), and optional `complaint_id`. Without a complaint ID it stages
an upload for the authenticated account. Registration receives its `upload_ids`;
those files must belong to the registering account, be unused and less than 24
hours old. File links and optional witnesses are saved atomically with the case.
A failed registration leaves its staged uploads reusable during that period.

Up to 20 uploads may accompany registration; each account can upload 50 files per
24 hours. `EVIDENCE_MAX_FILE_BYTES` controls each file's size (including videos).
Allowed extensions cover common images, MP4/MOV/WebM, MP3/WAV/M4A/OGG, PDF, Word,
ODT, CSV and text. All files remain in private evidence storage with opaque names,
size and SHA-256 checks. Download responses force attachments and never return
storage paths. File content is user-supplied; uploaded registration documents
still require officer review. Abandoned staged objects are retained for storage
reconciliation and cannot be attached after 24 hours.

`GET /complaints/{id}/uploads` and its `/{upload_id}/download` child authorize
complaint ownership or existing station/assignment scope. Commander full-docket
reads use `/dockets/{id}/full`; evidence downloads use
`/dockets/{id}/files/{file_id}/download`. Access and downloads are audited.

## Verification

Regression and new workflow tests use fake mail and rollback-only PostgreSQL
fixtures. Browser scripts `check_complaint_browser.py` and
`check_investigator_browser.py` exercise real Edge sessions and temporary private
storage. Actual SMTP inbox delivery is not tested by these automated checks.


## Local role test accounts and investigator availability

`scripts/create_test_users.py --credentials-file .cache/test-users.md` explicitly
creates preverified, local-development accounts for all seven roles and a second
investigator. Run it as a module from the project root (`python -m scripts.create_test_users`).
The command refuses production or non-loopback databases, never resets existing
accounts, and stores generated passwords only in the git-ignored credential file.
Reserved `example.invalid` addresses cannot receive emails, so these fixtures test
role workflows without testing SMTP verification or password-recovery delivery.
All station officers share Point station. Management can use the staff sign-in
and the aggregate management overview; national roles keep their existing scope.

Investigator candidates exclude anyone with an unended assignment on a docket
other than CLOSED or ARCHIVED. ON_HOLD cases still count as busy. Closing the case,
unassigning, or reassigning it releases that investigator. The server locks the
target officer and rechecks availability during assignment, so stale dropdowns or
simultaneous commanders cannot allocate two unfinished cases to the same person.
The dropdown reloads whenever assignment opens and after success or failure.
Existing case assignments are preserved; no cases are automatically reassigned.

## Shared case record and activity trail

Charge officers, station commanders and the assigned investigator see the same
complete case record within their existing station/assignment scope. Before a
docket exists, `/complaints/{id}/dossier` provides the original complaint,
contact and location details, statement history, witnesses and private uploads.
Commander review and assignment dialogs load the full record before enabling
their action. The investigator workspace includes the complete docket too.

The record includes decisions, approvals, assignments, status history, notes,
evidence and custody history, official feedback, documents and communications.
Its chronological activity trail identifies the actor, action and time using
case-linked audit records. Opening the complete record is itself audited.
Historical actions appear only where an audit record was originally captured.
Unrelated cases and authentication events are excluded.

The complainant navigation places **Track reference** beside **My complaints**,
**New complaint** and **Sign out**, opening its own tracking form.
