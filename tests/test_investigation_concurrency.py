"""Real multi-connection races in a disposable migrated database, never the app DB."""
from concurrent.futures import ThreadPoolExecutor
import re
import time
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.access.models import User, UserRole, Role
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.evidence.models import EvidenceItem, EvidenceCustodyEvent
from app.modules.evidence.schemas import CustodyRequest, EvidenceRequest
from app.modules.evidence.service import EvidenceService
from app.modules.investigations.models import CaseAssignment, DocketStatusHistory
from app.modules.investigations.schemas import StatusRequest
from app.modules.investigations.service import InvestigationService
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Station, Officer


@pytest.fixture(scope='module')
def race_database():
    name = 'test_c_race_' + uuid.uuid4().hex
    assert re.fullmatch(r'test_c_race_[0-9a-f]{32}', name)
    original = settings.migration_database_url
    owner_url = make_url(original)
    assert name != owner_url.database
    admin = create_engine(owner_url.set(database='postgres'), isolation_level='AUTOCOMMIT', hide_parameters=True)
    engine = None
    created = False
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created_oid = conn.execute(text('SELECT oid FROM pg_database WHERE datname=:name'), {'name': name}).scalar_one()
        created = True
        engine = create_engine(owner_url.set(database=name), hide_parameters=True)
        # Match the documented bootstrap prerequisite: Alembic's version table
        # lives in case_mgmt, so the schema must exist before the first revision.
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE SCHEMA case_mgmt AUTHORIZATION saps_owner')
            conn.exec_driver_sql('GRANT USAGE ON SCHEMA case_mgmt TO saps_api')
            conn.exec_driver_sql('ALTER DEFAULT PRIVILEGES IN SCHEMA case_mgmt GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO saps_api')
        settings.migration_database_url = owner_url.set(database=name).render_as_string(hide_password=False)
        try:
            command.upgrade(Config('alembic.ini'), 'head')
        finally:
            settings.migration_database_url = original
        yield engine
    finally:
        settings.migration_database_url = original
        if engine:
            engine.dispose()
        if created:
            with admin.connect() as conn:
                assert conn.execute(text('SELECT oid FROM pg_database WHERE datname=:name'), {'name': name}).scalar_one() == created_oid
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def race_case(race_database):
    with Session(race_database, expire_on_commit=False) as db:
        station = Station(station_code='RACE-' + uuid.uuid4().hex[:8], name='Race', province='Test')
        db.add(station)
        db.flush()
        officers = []
        role = db.scalar(select(Role.id).where(Role.code == 'INVESTIGATING_OFFICER'))
        for _ in range(3):
            unique = uuid.uuid4().hex
            user = User(username=unique, email=unique + '@example.invalid', password_hash='!')
            db.add(user)
            db.flush()
            officer = Officer(user_id=user.id, station_id=station.id, service_number=unique, rank='Test')
            db.add_all([officer, UserRole(user_id=user.id, role_id=role)])
            db.flush()
            officers.append(officer)
        person = Complainant(first_name='Test', last_name='Test', phone_number='000', preferred_contact_method='SMS')
        db.add(person)
        db.flush()
        complaint = Complaint(reference_number='RACE-' + uuid.uuid4().hex, complainant_id=person.id,
            station_id=station.id, channel='ONLINE', status='ACCEPTED', crime_category='Test',
            incident_description='Test', incident_location='Test', incident_province='Test')
        db.add(complaint)
        db.flush()
        decision = ComplaintDecision(complaint_id=complaint.id, decision='ACCEPTED', decided_by_officer_id=officers[0].id)
        db.add(decision)
        db.flush()
        docket = Docket(complaint_id=complaint.id, cas_number='RACE-' + uuid.uuid4().hex,
            created_from_decision_id=decision.id, opened_by_officer_id=officers[0].id, status='ACTIVE')
        db.add(docket)
        db.flush()
        db.add_all([CaseAssignment(docket_id=docket.id, investigating_officer_id=officers[0].id,
            assigned_by_officer_id=officers[0].id), DocketStatusHistory(docket_id=docket.id,
            from_status=None, to_status='ACTIVE', changed_by_user_id=officers[0].user_id)])
        db.commit()
        return docket.id, officers[0].user_id, [officer.id for officer in officers]


def race(engine, docket_id, operations):
    """Hold the docket until both real PostgreSQL backends are waiting for its lock."""
    def run(operation):
        with Session(engine) as db:
            db.execute(text('SET LOCAL ROLE saps_api'))
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            # Preload to exercise stale identity-map refresh as well as row locks.
            cached_docket = db.get(Docket, docket_id)
            try:
                operation(db)
                return 200
            except HTTPException as exc:
                return exc.status_code
    with engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(select(Docket.id).where(Docket.id == docket_id).with_for_update())
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(run, operation) for operation in operations]
            try:
                deadline = time.monotonic() + 8
                waiting = 0
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        waiting = observer.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name AND wait_event_type='Lock'"),
                            {'name': engine.url.database}).scalar_one()
                    if waiting >= 2:
                        break
                    time.sleep(0.02)
                assert waiting >= 2, 'Both database connections must contend for the docket lock'
            finally:
                transaction.rollback()
            return [future.result(timeout=15) for future in futures]


def test_concurrent_status_changes_have_one_winner(race_database, race_case):
    docket, actor, _ = race_case
    requests = [StatusRequest(status=status, expected_status='ACTIVE', reason='Race transition')
                for status in ('ON_HOLD', 'CLOSED')]
    operations = [lambda db, data=data: InvestigationService(db).update_status(actor, docket, data) for data in requests]
    assert sorted(race(race_database, docket, operations)) == [200, 409]
    with Session(race_database) as db:
        history = db.scalars(select(DocketStatusHistory).where(DocketStatusHistory.docket_id == docket)
            .order_by(DocketStatusHistory.changed_at)).all()
        assert len(history) == 2
        assert history[-1].to_status == db.get(Docket, docket).status
        assert history[-1].changed_by_user_id == actor
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_id == docket, AuditLog.action == 'case.update_status')) == 1


def test_concurrent_custody_transfers_have_one_winner(race_database, race_case):
    docket, actor, officers = race_case
    with Session(race_database) as db:
        registered = EvidenceService(db).register(actor, docket, EvidenceRequest(
            title='Test', description='Test', evidence_type='OTHER', is_digital=False, storage_location='Origin'))
    requests = [CustodyRequest(event_type='TRANSFERRED', expected_custody_event_id=registered.custody_version,
        to_custodian_officer_id=destination, to_location='Destination', notes='Race transfer') for destination in officers[1:]]
    def operation(db, data):
        cached_item = db.get(EvidenceItem, registered.id)
        return EvidenceService(db).custody(actor, registered.id, data)
    assert sorted(race(race_database, docket, [lambda db, data=data: operation(db, data) for data in requests])) == [200, 409]
    with Session(race_database) as db:
        events = db.scalars(select(EvidenceCustodyEvent).where(EvidenceCustodyEvent.evidence_item_id == registered.id)
            .order_by(EvidenceCustodyEvent.occurred_at)).all()
        assert len(events) == 2 and events[1].from_custodian_officer_id == officers[0]
        item = db.get(EvidenceItem, registered.id)
        assert item.current_custodian_officer_id == events[1].to_custodian_officer_id
        assert item.current_storage_location == events[1].to_location
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_id == item.id, AuditLog.action == 'evidence.custody.transferred')) == 1
