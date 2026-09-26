# Local demo officer setup

There is no officer creation/admin API in this checkout: public registration
creates complainants and rejects supplied roles. This explicit development seed
uses existing User, UserRole, Officer and Station models and the normal Argon2
password hashing. It does not change dashboards, migrations or authentication.

Run directly in your local IDE terminal, without output redirection or a terminal
transcript:

```powershell
.\.venv\Scripts\python.exe -m app.modules.stations.seed_demo_officers
```

Optional read-only preflight (no prompts or passwords):

```powershell
.\.venv\Scripts\python.exe -m app.modules.stations.seed_demo_officers --check
```

| Username | Only assigned role | Preferred station | Internal demo service number |
| --- | --- | --- | --- |
| demo_charge_officer | CHARGE_OFFICER | Point, KwaZulu-Natal | DEMO-LOCAL-CHARGE |
| demo_station_commander | STATION_COMMANDER (Captain rank) | Point, KwaZulu-Natal | DEMO-LOCAL-COMMANDER |
| demo_investigator | INVESTIGATING_OFFICER | Point, KwaZulu-Natal | DEMO-LOCAL-INVESTIGATOR |

All three use the same active station so the ordinary review/approval/assignment
workflow can be inspected. Point is preferred; if unavailable the command selects
an existing active KZN station, then another active station, deterministically.
It prints the actual station during preflight. These are project service numbers,
not official SAPS-issued service numbers.

## Email and password prompts

Email uniqueness is case-insensitive. The charge officer defaults to the address
specified by the user if unused, then the command prompts for the other two unique
inboxes. **In the inspected local database, that requested address already belongs
to a complainant.** It cannot be reused without violating uniqueness or changing
an existing account. The command therefore prompts for an unused charge-officer
replacement as well as the other two addresses. It does not convert that
complainant, add roles to it, use invented aliases or change its password.

Each newly created account receives a unique random 28-character starter password,
including uppercase, lowercase, digits and symbols. Only its normal password hash
is stored. After the entire transaction commits, the password is displayed once
in the local terminal; no credential file or audit payload stores it. Save it
privately at that point. A repeat run does not redisplay or reset passwords. The
current application has no forced-password-change/expiry or self-service password
reset screen, so "temporary" here means a generated starter credential, not an
implemented expiry policy. Losing it requires independently authorized recovery.

The command refuses piped/noninteractive creation so passwords are not captured
by automation logs. It is restricted to ENVIRONMENT=development and a loopback
database host (localhost, 127.0.0.1 or ::1). It is never run at startup. Creation
is serialized and atomic. Existing marked demo users are reused without mutations;
username/service/email collisions, deactivation or changed roles/stations cause a
clear stop. No existing user is adopted or privileged merely because a name matches.

## Sign in and inspect

Start the app in a normal terminal with SMTP network access:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

1. Charge officer: open `http://127.0.0.1:8000/officer/`, enter the username and
   generated password, then the six-digit code emailed to its own inbox. Inspect
   station complaints, walk-in intake, review, decisions and refusals.
2. Captain/commander: use the same `/officer/` page with the commander account.
   Inspect station complaints, escalations, docket approval/return and assignment.
3. Investigator: open `http://127.0.0.1:8000/investigator/`, follow its staff sign-in
   link, and use the investigator account and emailed code. Inspect assigned
   dockets, notes, status, evidence, protected files and custody.

Accounts start active but email-unverified with no enrolled authentication method
and no session. Setup sends no email. Normal password login initiates the existing
email verification flow; tokens are issued only after successful code verification.
There is no MFA/email bypass. Sign out between accounts, or use separate browser
profiles; staff sessions are shared within a tab.

These are the existing role workspaces/work queues, not new analytics dashboards.
D's analytics/notifications/documents UI remains unfinished and is not added here.
The investigator queue is empty until the commander approves a docket and assigns
that investigator. Staff only see their station's records; this command creates
no demo complaints, approvals, dockets, evidence or assignments.

## Verification and current state

The read-only preflight selected Point for all three accounts and confirmed that
none of these demo usernames had been created. Actual account creation is left
to your interactive terminal run because unused inboxes and private one-time
password display are required. Automated tests use unique temporary demo names,
fake mail and rollback-only database records, so they do not alter real demo users.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_demo_officers.py -q --tb=short
```

Verification completed: **17 focused demo-provisioning/email-authentication tests
passed**, with two existing dependency deprecation warnings; `git diff --check`
passed. Tests covered all three role sign-ins through email codes, repeatability,
unchanged hashes/IDs/station links, collision rollback, refusal to adopt unrelated
users, local-development guards and refusal of noninteractive password output.
No real accounts or email deliveries were created by these rollback-only tests.
