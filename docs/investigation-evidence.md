# Collaborator C: investigation and evidence APIs

Built on the merged complaint and docket APIs at `a8898e5`. No model, seed, or
migration changes. The shared counter allocator now also issues prototype
`EVD-{station}-{Johannesburg year}-{number}` references in the evidence transaction.

## Access and workflow

Every route requires an active authenticated MFA/session user and its existing
RBAC permission. The service also checks active officer, active station, role,
station membership, and (for investigator operations) current assignment.
Unknown and out-of-scope dockets return 404. A commander does not inherit evidence
management simply by being a supervisor. Administrative officer provisioning
and officer-directory APIs remain with the lead/shared package.

1. A registers the complaint; B accepts it, opens the docket and approves it.
2. A station commander assigns an active investigating officer at the same station.
   The initial assignment moves APPROVED to ACTIVE and inserts status history.
3. The assigned investigator reads the docket, adds notes and registers evidence.
4. Reassignment closes the previous assignment before inserting its replacement.
   The former investigator loses access immediately; historical rows remain.
5. The investigator may move ACTIVE to ON_HOLD and back, or close either state.
   Closing additionally requires `case.close` and records closure time/reason.
   Closed/archived dockets reject new notes, assignments and evidence mutations.
   Current assignees can still read retained records. Reopening/archiving is not
   introduced by C. Ending an assignment does not invent a new docket status.

All C mutations serialize on the docket row before assignment checks or evidence
row locks. Mutations and audit events commit together. Sensitive reads/downloads
are audited. Audit payloads contain actor, station, action and entity identifiers,
not note content, original filenames, file bytes, paths or credentials. Notes,
file versions and custody events have no update/delete APIs and retain database
append-only protection. Correct a note by appending a CORRECTION note.

## Routes

All paths below begin with `/api/v1`. List endpoints accept `limit` (1–100,
default 50) and `offset` (>=0), returning arrays in a stable order.

| Method | Path | Permission |
| --- | --- | --- |
| POST | `/dockets/{id}/assignments` | `docket.assign` |
| POST | `/dockets/{id}/assignments/end` | `docket.assign` |
| GET | `/dockets/{id}/assignments` | `docket.assign` |
| GET | `/investigations/dockets` | `docket.view_assigned` |
| GET | `/investigations/dockets/{id}` | `docket.view_assigned` |
| POST | `/dockets/{id}/notes` | `case.add_note` |
| GET | `/dockets/{id}/notes` | `docket.view_assigned` |
| POST | `/dockets/{id}/status` | `case.update_status`, plus `case.close` for closure |
| POST, GET | `/dockets/{id}/evidence` | `evidence.manage` |
| GET | `/evidence/{id}` | `evidence.manage` |
| POST | `/evidence/{id}/custody-events` | `evidence.manage` |
| GET | `/evidence/{id}/custody-events` | `evidence.view_custody` |
| POST, GET | `/evidence/{id}/files` | `evidence.manage` |
| GET | `/evidence/{id}/files/{file_id}/download` | `evidence.manage` |

The existing B route `/dockets/{id}` remains commander-only. Investigators use
the separate `/investigations/dockets/{id}` route. Consult `/docs` for complete
request/response schemas; unknown request properties are rejected.

Assignment example:

```json
{"investigating_officer_id":"<officer UUID>","reason":"Assigned for investigation"}
```

Evidence registration example:

```json
{
  "title":"Camera recording",
  "description":"Recording supplied for the investigation",
  "evidence_type":"VIDEO",
  "is_digital":true,
  "storage_location":"Secure evidence store"
}
```

Registration records the assigned investigator as initial custodian and appends
a REGISTERED event; current state becomes IN_CUSTODY. Optional collection time
must be timezone-aware and not in the future; when supplied the registering
investigator is recorded as collector. Third-party collection attribution needs
a separately agreed workflow rather than accepting an arbitrary actor ID.

Custody changes require a reason (`notes`) and destination location. TRANSFERRED
also requires an active same-station investigating officer as destination.
The assigned investigator records the handover; actual source custodian/location
come from the database, never from client-supplied fields. Reassignment alone
does not silently transfer custody. A custody record does not grant API access.

Allowed changes:

- IN_CUSTODY or TRANSFERRED: transfer, start analysis, release or dispose.
- UNDER_ANALYSIS: complete analysis, returning to IN_CUSTODY.
- RELEASED and DISPOSED: terminal; retain history and existing file access.

Downloads append ACCESSED without changing current custody. This is a prototype
recording workflow; cross-station custody, recipient countersignatures and legal
disposal authorization are not represented as completed features.

## Protected file storage

POST `/evidence/{id}/files` accepts multipart field `file`, not client-supplied
storage keys, hashes or sizes. Example with an existing bearer token:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/evidence/<id>/files" `
  -H "Authorization: Bearer <access-token>" -F "file=@C:/path/recording.mp4"
```

Set `EVIDENCE_STORAGE_PATH` to a private directory writable only by the service
account (configure Windows ACLs on deployment). The default is the Git-ignored
`.evidence-storage` directory under the repository. It is created only upon an
upload; no public static mount or direct object URL is provided. Use durable
storage shared by all API workers and back it up with the database. Keep the
directory outside web-server public roots.

`EVIDENCE_MAX_FILE_BYTES` defaults to 26,214,400 (25 MiB), maximum configurable
100 MiB. Empty/oversized files are rejected. Multipart request bodies are also
bounded before spooling (file limit plus 1 MiB overhead), including bodies without
Content-Length. Align ingress request-size and timeout limits with this setting.

Uploads use exclusive, random object keys; filenames are display metadata only.
SHA-256 and size are calculated from actual bytes. Version allocation holds the
evidence lock. There is no overwrite operation and binary data stays outside
PostgreSQL. API responses omit storage keys. Before download, bounded content is
read and checked against its stored size and hash; mismatch returns 409, missing
objects return 503. Responses are attachments with `application/octet-stream`,
`nosniff` and `no-store`. Files are not rendered by the application. All C routes
participate in the existing production HTTPS policy.

Storage and PostgreSQL do not share an atomic transaction. Pre-commit failures
remove newly written objects; if COMMIT has an uncertain outcome, the object is
retained rather than risking loss of committed evidence. A crash or cleanup
failure can leave private orphan objects. Reconcile these against committed
metadata before removing anything; automated reconciliation and malware scanning
are not included. Do not lower the file limit below existing file sizes without
planning how those files will be accessed. Downloads currently buffer one bounded
file for integrity verification; size concurrent worker capacity accordingly.

## Verification and handoff

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider
```

`tests/test_investigation_api.py` uses B's rollback fixture and actual A/B APIs to
prepare approved dockets, then switches C operations to the restricted runtime
role. Files use pytest temporary directories. Coverage includes assignment
history/revocation, station scope, role/permission checks, notes, status history,
closure, custody transitions, uploads, versions, downloads, tampering, missing
files, request/file size limits and audit-failure rollback. Real multi-connection
race tests, load testing and deployment ACL verification remain shared QA work.

C leaves D's feedback, notifications, documents, alerts and dashboards untouched.
Shared QA can extend the existing audit events; do not duplicate numbering,
authentication, A's complaint flow or B's decision/approval flow. No new migration
or permission seed is needed to merge this branch.
