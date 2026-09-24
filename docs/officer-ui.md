# Charge officer and commander UI

The Collaborator B interface is a dependency-free HTML, CSS and vanilla JavaScript
application served by FastAPI at `http://127.0.0.1:8000/officer/`.

Start the API from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The interface supports existing officer accounts through password and MFA login.
Navigation is derived from live roles and permissions returned by `/api/v1/auth/me`.

## Charge officer

- List complaints belonging to the officer's station.
- Start review of a submitted complaint.
- Accept or refuse an under-review complaint.
- Select active controlled refusal reasons and supply required notes.
- Accept a criminal complaint and receive its automatically created docket/CAS.
- Register a new walk-in complainant and complaint at the officer's station.
- View and append actual complainant/witness statements and initial evidence before
  commander approval; statement corrections preserve all previous versions.
- Recover legacy accepted complaints via the idempotent compatibility endpoint.

## Station commander

- View station complaints.
- View, acknowledge and resolve in-scope refusal escalations.
- List station dockets and review pending dockets.
- Read statements, witnesses and the initial evidence register for review.
- Approve or return a docket for correction.
- Select an active investigating officer from the same station and assign the case.

NCC users see only their authorised NCC escalation queue. The UI does not provide
complainant registration/tracking, investigator notes/evidence, feedback,
notifications, documents, alerts or analytics.

The supporting queue endpoints enforce role and station scope in services and audit
sensitive reads. The UI never treats hidden navigation as authorization. Access
tokens are kept in `sessionStorage` and cleared on sign-out or refresh failure.

Run the focused tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_officer_work_queue.py tests/test_decisions_dockets.py -q
```

No schema change or migration is required.
