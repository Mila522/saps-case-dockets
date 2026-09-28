"""Create the three demo staff accounts non-interactively (LOCAL TESTING ONLY).

Put this file in the project's `scripts/` folder, then run from the project root:

    .\\.venv\\Scripts\\python.exe -m scripts.create_test_staff you@gmail.com

One Gmail address is enough: the script derives you+charge@gmail.com,
you+commander@gmail.com and you+investigator@gmail.com. Gmail delivers all of
them to your inbox, and the app sees them as three different, unused emails.

Or pass three separate addresses (charge, commander, investigator):

    .\\.venv\\Scripts\\python.exe -m scripts.create_test_staff a@x.com b@x.com c@x.com

It reuses the project's own provisioning logic, so it keeps the same safety
rules: ENVIRONMENT=development and a localhost database only. Login still needs
the 6-digit code that is emailed to each account.
"""
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.modules.stations.seed_demo_officers import SPECS, provision


def build_emails(args):
    if len(args) == 1:
        local, _, domain = args[0].partition('@')
        if not local or not domain:
            raise ValueError('Give a valid email address.')
        tags = ('charge', 'commander', 'investigator')
        return {spec[0]: f'{local}+{tag}@{domain}' for spec, tag in zip(SPECS, tags)}
    if len(args) == 3:
        return {spec[0]: email for spec, email in zip(SPECS, args)}
    raise ValueError('Pass either one email address or exactly three (charge, commander, investigator).')


def main():
    try:
        emails = build_emails(sys.argv[1:])
        with SessionLocal() as db:
            with db.begin():
                results = provision(db, emails)
    except (ValueError, SQLAlchemyError) as error:
        raise SystemExit(f'Failed, nothing was created: {error}') from None

    print()
    for result in results:
        print(f'{result.username} | {result.role} | {result.station} | {emails.get(result.username)}')
        if result.temporary_password:
            print('   password (shown once): ' + result.temporary_password.get_secret_value())
        else:
            print('   already exists; use its original password')
    print('\nSave these passwords now. Sign in at /officer/ or /investigator/ and enter the emailed code.')


if __name__ == '__main__':
    main()