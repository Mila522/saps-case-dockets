"""RBAC and counter invariants; counter writes are rolled back."""
import unittest
import uuid

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.db.session import engine
from app.modules.access.models import Permission, Role, RolePermission
from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter


EXPECTED = {
    'COMPLAINANT': 'complaint.submit complaint.view_own case.track_own confirmation.download',
    'CHARGE_OFFICER': 'complaint.register complaint.view_station complaint.decide refusal.record confirmation.download',
    'STATION_COMMANDER': 'complaint.view_station refusal.escalation.view docket.approve docket.assign audit.view_station dashboard.view_station alert.view confirmation.download',
    'INVESTIGATING_OFFICER': 'docket.view_assigned case.update_status case.add_note case.close evidence.manage evidence.view_custody feedback.provide confirmation.download',
    'SYSTEM_ADMINISTRATOR': 'user.manage role.manage permission.manage station.manage audit.view_all dashboard.view_all alert.view',
    'SAPS_MANAGEMENT': 'audit.view_all dashboard.view_all alert.view',
    'NCC_OFFICER': 'refusal.escalation.view dashboard.view_all alert.view',
}


class DatabaseCompletionTests(unittest.TestCase):
    def test_rbac_mappings(self):
        with engine.connect() as conn:
            rows = conn.execute(select(Role.code, Permission.code).select_from(RolePermission).join(Role).join(Permission)).all()
            expected = {(role, p) for role, permissions in EXPECTED.items() for p in permissions.split()}
            self.assertEqual(set(rows), expected)
            self.assertEqual(len(rows), 38)
            self.assertEqual(len(rows), len(set(rows)))
            self.assertFalse(conn.execute(text("SELECT has_function_privilege('saps_api', 'case_mgmt.undo_rbac_seed_1edbbd1f3330()', 'EXECUTE')")).scalar_one())

    def test_counter_constraints_and_atomic_increment_rollback(self):
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                station = conn.execute(Station.__table__.insert().values(station_code=uuid.uuid4().hex, name="Test", province="Test").returning(Station.id)).scalar_one()
                table = IdentifierCounter.__table__
                key = dict(counter_type="COMPLAINT", station_id=station, calendar_year=2026)
                conn.execute(table.insert().values(**key))
                self.assertEqual(conn.execute(select(table.c.last_value).where(table.c.station_id == station)).scalar_one(), 0)
                for change in ({"counter_type": "INVALID"}, {"calendar_year": 1999}, {"last_value": -1}):
                    with self.assertRaises(IntegrityError):
                        with conn.begin_nested():
                            conn.execute(table.insert().values(**(key | change)))
                with self.assertRaises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(table.insert().values(**key))
                # SQL verification only, not an application generation service.
                statement = insert(table).values(**key, last_value=1).on_conflict_do_update(
                    index_elements=[table.c.counter_type, table.c.station_id, table.c.calendar_year],
                    set_={"last_value": table.c.last_value + 1},
                ).returning(table.c.last_value)
                self.assertEqual(conn.execute(statement).scalar_one(), 1)
                self.assertEqual(conn.execute(statement).scalar_one(), 2)
                with conn.begin_nested() as savepoint:
                    self.assertEqual(conn.execute(statement).scalar_one(), 3)
                    savepoint.rollback()
                self.assertEqual(conn.execute(statement).scalar_one(), 3)
                for other in (key | {"calendar_year": 2027}, key | {"counter_type": "CAS"}):
                    conn.execute(table.insert().values(**other))
                with self.assertRaises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(Station.__table__.delete().where(Station.id == station))
                pk = inspect(conn).get_pk_constraint("identifier_counters", schema="case_mgmt")
                self.assertEqual(pk['constrained_columns'], ['counter_type', 'station_id', 'calendar_year'])
                checks = inspect(conn).get_check_constraints("identifier_counters", schema="case_mgmt")
                self.assertEqual({c['name'] for c in checks}, {'ck_identifier_counters_counter_type', 'ck_identifier_counters_calendar_year', 'ck_identifier_counters_last_value'})
                self.assertTrue(all(conn.execute(text("""SELECT has_table_privilege('saps_api', 'case_mgmt.identifier_counters', p)
                    FROM unnest(ARRAY['SELECT','INSERT','UPDATE']) p""")).scalars()))
            finally:
                transaction.rollback()
