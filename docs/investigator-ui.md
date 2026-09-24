# Collaborator C investigator workspace

Open **`/investigator/`** on the running FastAPI application. The entry page links to the existing staff login/MFA flow with a fixed investigator return destination. Start the app with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Approach and ownership

The merged project already had vanilla JavaScript frontends. C uses HTML/CSS/ES modules, B's navy/gold stylesheet and existing login/MFA, and A's reusable `createClient` API client. There is no frontend framework, package installation, CDN, or build step. The UI is explicitly marked as a prototype, with no official logo.

Pages share one static shell; their URLs and modules are:

| Page | URL | Module |
| --- | --- | --- |
| Assigned dockets | `/investigator/` | `investigator-dockets.mjs` |
| Docket details, notes, evidence register, status history | `/investigator/docket?id=<uuid>` | `docket-detail.mjs` |
| Evidence details, files and custody | `/investigator/evidence?id=<uuid>` | `evidence-detail.mjs` |

`app.mjs` handles the session, page selection, busy states, forms and failures. `ui.mjs` supplies escaped rendering, accessible fields, notices, tables and native confirmation dialogs. `contracts.mjs` contains request enums checked against OpenAPI in tests. Lists follow all API pages before applying client-side filters or reporting counts.

Only C's investigation/evidence contracts were extended. No database models, migrations or D backend modules were changed. The existing A-to-B-to-C integration test now follows complaint → review → acceptance → docket → approval → assignment → note → evidence → status, then tests protected files, custody and append-only history.

## Integrated API contract

All paths below are relative to `/api/v1`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/login` | Existing staff login screen |
| POST | `/auth/mfa/setup` | Existing MFA enrolment |
| POST | `/auth/mfa/verify-setup` | Complete enrolment |
| POST | `/auth/mfa/verify` | Existing MFA verification |
| GET | `/auth/me` | Authoritative username, roles and permissions |
| POST | `/auth/refresh` | Rotate the existing session tokens |
| POST | `/auth/logout` | Revoke the server session |
| GET | `/investigations/dockets` | Assigned work queue |
| GET | `/investigations/dockets/{id}` | Authorised detail and allowed next statuses |
| GET | `/investigations/dockets/{id}/status-history` | Immutable status history |
| GET, POST | `/dockets/{id}/notes` | Read/append notes |
| POST | `/dockets/{id}/status` | Status change with `expected_status` |
| GET, POST | `/dockets/{id}/evidence` | Evidence register |
| GET | `/evidence/{id}` | Evidence and current custody version |
| GET | `/evidence/{id}/custodians` | Eligible same-station investigators |
| GET, POST | `/evidence/{id}/custody-events` | Timeline and version-checked changes |
| GET, POST | `/evidence/{id}/files` | File metadata history and protected multipart upload |
| GET | `/evidence/{id}/files/{file_id}/download` | Authorised integrity-checked download |

The two previously uncommitted C support endpoints (status history and custodians), and the detailed incident fields, are used as supplied. `allowed_next_statuses` was added to the investigator response using the same transition rules as the backend mutation, filtered by current permissions. All backend access checks remain mandatory.

## Authentication and error handling

The investigator workspace uses the staff portal's existing `sessionStorage` keys. These hold tokens only, not trusted roles. A's existing portal remains memory-only. The shared API client attaches bearer tokens, handles JSON/multipart/binary responses, coalesces simultaneous refreshes, retries at most once after a 401, and clears local tokens on refresh failure or logout—even if logout cannot reach the server. Login/MFA is reused rather than recreated. Credentials and tokens are never logged or included in URLs.

401 returns to the sign-in state. 403/404 removes private record content and shows a privacy-safe message. 409 refreshes the current docket or custody state and requires a new confirmation. Validation errors are attached to labelled fields; submissions trim text, reject whitespace-only values and block duplicate submissions. A failed request with an uncertain write outcome advises refreshing before retrying.

## Evidence files and missing contracts

Binary upload already exists, so this UI uses it. It does not offer a misleading metadata-only form. Positive file size is checked in the browser and backend; the server calculates SHA-256, media type and version. The UI displays the returned immutable metadata and offers protected downloads. It never accepts storage keys, invents public storage links, or displays storage credentials.

The `/auth/me` response does not include station name. The queue shows the station from authorised assigned dockets; with no assignments it explains that station information is not available yet. History endpoints expose officer/user IDs rather than names for every historical actor; the UI uses the signed-in username and authorised custodian names where possible, otherwise the returned identifier. No unrestricted officer directory is queried.

No separate closure-feedback-pending flag is returned. C does not invent one or call D's feedback, notifications, document, alert or dashboard endpoints. Existing server-side integrations continue unchanged.

## Responsive and accessible behaviour

The workspace uses labelled fields, table headers, text status badges, visible keyboard focus, a skip link, semantic sections and a native keyboard-operable confirmation dialog. Error/success notices receive focus. Long IDs and hashes wrap. Tables turn into labelled record cards below 1100px; navigation and forms stack on narrow screens. The reused staff sign-in gets a scoped mobile override only when entering the investigator workspace.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
node --test tests/frontend/api-client.test.mjs
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\alembic.exe check
.\.venv\Scripts\python.exe scripts/check_investigator_browser.py
```

Node is optional for the dependency-free API-client checks; it is not needed to run or build the application. The browser script uses locally installed Microsoft Edge and the existing Python dependencies. It runs Uvicorn with rollback-only fixtures, actual registration/MFA and real API requests. Temporary browser profiles and uploads are removed. Screenshots in `docs/investigator-preview/` contain synthetic fixture data only.

The smoke tests cover the three page URLs, assets, MIME types, security headers, coexistence with A/B/API/docs, no embedded frontend credentials, enum drift, station/assignment scope and closure permission. Existing integration tests verify unassigned-investigator rejection, complainant isolation and database-enforced immutable notes, files, custody and status history. API-client checks cover refresh concurrency, retry limits, logout failure, multipart, validation errors and safe text rendering.

Verified on 24 September 2026:

- Complete backend suite: **189 passed**, two existing dependency deprecation warnings.
- API-client tests: **9 passed**.
- Migration current and head: **089c746ed6a9**; `alembic check`: **no new upgrade operations**.
- Real Edge/Uvicorn browser workflow passed: existing login and MFA setup, assigned queue/search/empty state, duplicate-note prevention, evidence registration, protected file upload/metadata, status success/conflict, custody success/conflict, refresh/expiry/logout, assignment revocation, permission denial and retry after a temporary profile-service failure.
- Screenshot review and overflow checks passed at 1440px desktop and 390px mobile widths.
- Live application HTTP checks returned 200 for `/docs`, `/redoc`, `/openapi.json`, `/health/database`, A's portal, B's workspace, all three C pages and C's entry module. ES modules are served as `text/javascript`, including on Windows.
- `git diff --check` passed. No outstanding implementation blocker was found.

## Files to review/stage for C

New C-owned files:

```text
app/frontend/investigator_routes.py
frontend/investigator/index.html
frontend/investigator/assets/app.mjs
frontend/investigator/assets/contracts.mjs
frontend/investigator/assets/ui.mjs
frontend/investigator/assets/investigator-dockets.mjs
frontend/investigator/assets/docket-detail.mjs
frontend/investigator/assets/evidence-detail.mjs
frontend/investigator/assets/investigator.css
tests/test_investigator_frontend.py
tests/frontend/api-client.test.mjs
scripts/check_investigator_browser.py
docs/investigator-ui.md
docs/investigator-preview/queue-desktop.png
docs/investigator-preview/docket-mobile.png
docs/investigator-preview/evidence-desktop.png
docs/investigator-preview/evidence-mobile.png
```

Existing C-owned files (the six investigation/evidence files already contained local changes before UI work):

```text
app/modules/investigations/router.py
app/modules/investigations/schemas.py
app/modules/investigations/service.py
app/modules/evidence/router.py
app/modules/evidence/schemas.py
app/modules/evidence/service.py
tests/test_investigation_contracts.py
tests/test_end_to_end_case_workflow.py
```

Required shared integration files; stage their reviewed changes with C:

```text
app/main.py                         # C mount and portable JavaScript MIME mappings
app/frontend/api.mjs               # shared refresh, multipart, binary and safe logout
app/frontend/app.mjs               # clear A's screen even if logout fails
frontend/officer/assets/app.js     # fixed C return after existing login/MFA
frontend/officer/assets/styles.css # scoped mobile investigator sign-in
```

Do not stage `.env`, private upload storage, temporary browser profiles or test caches. No files are automatically staged, committed or pushed by this frontend work.
