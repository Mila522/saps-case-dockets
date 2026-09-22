"""Verify migrations only in a newly created, exactly named disposable database.

Run from the repository root: python -m scripts.verify_migration_chain
Existing databases are never reused/dropped. Failed verification leaves the
new database for inspection; successful verification drops only that database.
"""
import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings


TEMP_DATABASE = "saps_case_docket_migration_test"


def run():
    owner_url = make_url(settings.migration_database_url)
    runtime_url = make_url(settings.database_url)
    if owner_url.database == TEMP_DATABASE or runtime_url.database == TEMP_DATABASE:
        raise RuntimeError("Run this verifier from the normal development configuration")
    admin = create_engine(owner_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        allowed = conn.execute(text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
        if not allowed:
            print("SKIPPED: migration account cannot create databases; no elevation requested.")
            admin.dispose()
            return
        if conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=:name)"), {"name": TEMP_DATABASE}).scalar_one():
            print("SKIPPED: exact temporary database already exists; it will not be reused or dropped.")
            admin.dispose()
            return
        # Literal, fixed identifier; no variable-derived destructive target.
        conn.exec_driver_sql('CREATE DATABASE saps_case_docket_migration_test')
        created_oid = conn.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": TEMP_DATABASE}).scalar_one()
    temporary = create_engine(owner_url.set(database=TEMP_DATABASE))
    try:
        with temporary.begin() as conn:
            # Infrastructure prerequisites from the original project setup.
            # No model tables are created outside Alembic.
            conn.exec_driver_sql('CREATE SCHEMA case_mgmt')
            conn.exec_driver_sql('GRANT USAGE ON SCHEMA case_mgmt TO saps_api')
            conn.exec_driver_sql('ALTER DEFAULT PRIVILEGES IN SCHEMA case_mgmt GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO saps_api')
            conn.exec_driver_sql('ALTER DATABASE saps_case_docket_migration_test SET search_path TO case_mgmt, public')
        child_env = os.environ.copy()
        child_env['MIGRATION_DATABASE_URL'] = owner_url.set(database=TEMP_DATABASE).render_as_string(hide_password=False)
        child_env['DATABASE_URL'] = runtime_url.set(database=TEMP_DATABASE).render_as_string(hide_password=False)

        def alembic(*args):
            subprocess.run([sys.executable, '-m', 'alembic', *args], env=child_env, check=True)

        # Include a pre-existing grant to prove downgrade ownership is precise.
        alembic('upgrade', 'a59515a63a4d')
        with temporary.begin() as conn:
            conn.exec_driver_sql("""INSERT INTO case_mgmt.role_permissions(role_id, permission_id)
                SELECT r.id, p.id FROM case_mgmt.roles r CROSS JOIN case_mgmt.permissions p
                WHERE r.code='COMPLAINANT' AND p.code='complaint.submit'""")
        alembic('upgrade', 'head')
        alembic('check')
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=child_env, check=True)
        alembic('downgrade', 'a59515a63a4d')
        with temporary.connect() as conn:
            rows = conn.execute(text("""SELECT r.code, p.code FROM case_mgmt.role_permissions rp
                JOIN case_mgmt.roles r ON r.id=rp.role_id JOIN case_mgmt.permissions p ON p.id=rp.permission_id""")).all()
            assert rows == [('COMPLAINANT', 'complaint.submit')], "Downgrade changed a pre-existing grant"
        # This command runs only against the created temporary database.
        alembic('downgrade', 'base')
        alembic('upgrade', 'head')
        alembic('check')
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=child_env, check=True)
    except BaseException:
        print("Verification failed; newly created test database retained. The main database was not changed.")
        raise
    else:
        temporary.dispose()
        with admin.connect() as conn:
            current_oid = conn.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": TEMP_DATABASE}).scalar_one()
            if current_oid != created_oid:
                raise RuntimeError("Temporary database identity changed; refusing to drop it")
            conn.exec_driver_sql('DROP DATABASE saps_case_docket_migration_test')
        print("SUCCESS: full upgrade/downgrade/replay, existing-grant preservation, schema check, and test suite passed; newly created test database dropped.")
    finally:
        temporary.dispose()
        admin.dispose()


if __name__ == '__main__':
    run()
