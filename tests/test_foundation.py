"""Run against the migrated local database: python -m unittest discover -s tests -v."""
import json
import socket
import threading
import time
import unittest
import urllib.request
import uuid

import uvicorn
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import engine
from app.main import app
from app.modules.access.models import Permission, Role, User, UserRole
from app.modules.complainants.models import Complainant
from app.modules.stations.models import Officer, Station


class FoundationTests(unittest.TestCase):
    def test_schema_and_runtime_privileges(self):
        configure_mappers()
        with engine.connect() as conn:
            self.assertEqual(tuple(conn.execute(text(
                "SELECT current_database(), current_user, current_schema()"
            )).one()), (engine.url.database, "saps_api", "case_mgmt"))
            inspector = inspect(conn)
            self.assertEqual(set(inspector.get_table_names(schema="case_mgmt")),
                             {table.name for table in Base.metadata.tables.values()} | {"alembic_version"})
            for table in Base.metadata.sorted_tables:
                conn.execute(select(table).limit(1))
                self.assertEqual(inspector.get_pk_constraint(table.name, schema="case_mgmt")["constrained_columns"],
                                 [column.name for column in table.primary_key])
                actual_fks = inspector.get_foreign_keys(table.name, schema="case_mgmt")
                self.assertEqual({fk["name"]: fk["options"].get("ondelete") for fk in actual_fks},
                                 {conn.dialect.identifier_preparer.format_constraint(fk): fk.ondelete
                                  for fk in table.foreign_key_constraints})
                unique_columns = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name, schema="case_mgmt")}
                unique_columns |= {tuple(i["column_names"]) for i in inspector.get_indexes(table.name, schema="case_mgmt") if i["unique"]}
                for column in table.columns:
                    if column.unique:
                        self.assertIn((column.name,), unique_columns)
                columns = {c["name"]: c for c in inspector.get_columns(table.name, schema="case_mgmt")}
                for column in table.columns:
                    self.assertEqual(columns[column.name]["nullable"], column.nullable)
                    if hasattr(column.type, "timezone"):
                        self.assertTrue(columns[column.name]["type"].timezone)
                    if column.server_default is not None:
                        self.assertIsNotNone(columns[column.name]["default"])
            checks = inspector.get_check_constraints("complainants", schema="case_mgmt")
            self.assertEqual([c["name"] for c in checks], ["ck_complainants_preferred_contact_method"])
            self.assertEqual(conn.execute(text("SELECT version_num FROM case_mgmt.alembic_version")).scalar_one(),
                             ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())

    def test_exact_seeds(self):
        expected_roles = "COMPLAINANT CHARGE_OFFICER STATION_COMMANDER INVESTIGATING_OFFICER SYSTEM_ADMINISTRATOR SAPS_MANAGEMENT NCC_OFFICER".split()
        expected_permissions = "complaint.submit complaint.register complaint.view_own complaint.view_station complaint.decide refusal.record refusal.escalation.view confirmation.download docket.approve docket.assign docket.view_assigned case.track_own case.update_status case.add_note case.close evidence.manage evidence.view_custody feedback.provide audit.view_station audit.view_all dashboard.view_station dashboard.view_all alert.view user.manage role.manage permission.manage station.manage".split()
        with engine.connect() as conn:
            for model, codes, prefix in ((Role, expected_roles, "1"), (Permission, expected_permissions, "2")):
                self.assertEqual(dict(conn.execute(select(model.code, model.id)).all()),
                                 {code: uuid.UUID(f"{prefix}0000000-0000-4000-8000-{i:012d}") for i, code in enumerate(codes, 1)})
            self.assertTrue(all(conn.execute(select(Role.is_system_role)).scalars()))

    def test_constraints_and_deletion_rollback(self):
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                def rejected(statement):
                    with self.assertRaises(IntegrityError):
                        with conn.begin_nested():
                            conn.execute(statement)
                user_id = conn.execute(User.__table__.insert().values(
                    username="foundation-test-" + uuid.uuid4().hex,
                    email=uuid.uuid4().hex + "@example.invalid", password_hash="!unusable-test-account"
                ).returning(User.id)).scalar_one()
                station_id = conn.execute(Station.__table__.insert().values(
                    station_code=uuid.uuid4().hex, name="Test station", province="Test"
                ).returning(Station.id)).scalar_one()
                officer_id = conn.execute(Officer.__table__.insert().values(
                    user_id=user_id, station_id=station_id, service_number=uuid.uuid4().hex, rank="Test"
                ).returning(Officer.id)).scalar_one()
                rejected(User.__table__.delete().where(User.id == user_id))
                rejected(Station.__table__.delete().where(Station.id == station_id))
                conn.execute(Officer.__table__.delete().where(Officer.id == officer_id))
                complainant = dict(first_name="Test", last_name="Test", phone_number="000", preferred_contact_method="SMS")
                conn.execute(Complainant.__table__.insert().values(**complainant))
                conn.execute(Complainant.__table__.insert().values(**complainant))
                complainant_id = conn.execute(Complainant.__table__.insert().values(user_id=user_id, **complainant).returning(Complainant.id)).scalar_one()
                rejected(Complainant.__table__.insert().values(user_id=user_id, **complainant))
                rejected(Complainant.__table__.insert().values(**(complainant | {"preferred_contact_method": "POST"})))
                role_id = conn.execute(select(Role.id).limit(1)).scalar_one()
                assignment = UserRole.__table__.insert().values(user_id=user_id, role_id=role_id)
                conn.execute(assignment)
                rejected(assignment)
                conn.execute(User.__table__.delete().where(User.id == user_id))
                self.assertIsNone(conn.execute(select(Complainant.user_id).where(Complainant.id == complainant_id)).scalar_one())
                self.assertIsNone(conn.execute(select(UserRole.user_id).where(UserRole.user_id == user_id)).first())
            finally:
                transaction.rollback()

    def test_http_health(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            self.assertTrue(server.started)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/database", timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {"status": "connected", "database": engine.url.database, "user": "saps_api", "schema": "case_mgmt"})
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
