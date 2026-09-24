"""Acceptance races use separate PostgreSQL connections in a disposable migrated DB."""
from concurrent.futures import ThreadPoolExecutor
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from test_investigation_concurrency import race_database
from app.modules.access.models import User, Role, UserRole
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.dockets.service import DocketService
from app.modules.refusals.models import ComplaintDecision
from app.modules.refusals.schemas import ComplaintDecisionRequest
from app.modules.refusals.service import ComplaintDecisionService
from app.modules.investigations.models import DocketStatusHistory
from app.modules.stations.models import Station, Officer
from app.modules.system.models import IdentifierCounter
from app.modules.communications.models import Notification
from app.modules.audit.models import AuditLog


def setup_case(engine):
    with Session(engine) as db:
        station=Station(station_code='ACCEPT-'+uuid.uuid4().hex[:8],name='Race station',province='Test')
        actor=User(username=uuid.uuid4().hex,email=uuid.uuid4().hex+'@example.invalid',password_hash='!')
        person=Complainant(first_name='Test',last_name='Person',phone_number='000',preferred_contact_method='PHONE')
        db.add_all([station,actor,person]);db.flush()
        officer=Officer(user_id=actor.id,station_id=station.id,service_number=uuid.uuid4().hex,rank='Test')
        role=db.scalar(select(Role.id).where(Role.code=='CHARGE_OFFICER'))
        db.add_all([officer,UserRole(user_id=actor.id,role_id=role)])
        row=Complaint(reference_number='RACE-'+uuid.uuid4().hex,complainant_id=person.id,station_id=station.id,
            channel='ONLINE',status='SUBMITTED',crime_category='Theft',incident_description='Test statement',
            incident_location='Test',incident_province='Test')
        db.add(row);db.flush()
        result=(station.id,actor.id,row.id)
        db.commit();return result


def contend(engine,complaint_id,operations):
    def worker(operation):
        with Session(engine) as db:
            db.execute(text('SET LOCAL ROLE saps_api'))
            db.execute(text("SET LOCAL lock_timeout='10s'"))
            cached=db.get(Complaint,complaint_id)  # Must be refreshed after the lock wait.
            try:
                operation(db)
                return 200
            except HTTPException as error:
                return error.status_code
    with engine.connect() as blocker:
        transaction=blocker.begin()
        blocker.execute(select(Complaint.id).where(Complaint.id==complaint_id).with_for_update())
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker,operation) for operation in operations]
            try:
                deadline=time.monotonic()+8
                waiting=0
                while time.monotonic()<deadline:
                    with engine.connect() as observer:
                        waiting=observer.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name AND wait_event_type='Lock'"),{'name':engine.url.database}).scalar_one()
                    if waiting>=2:break
                    time.sleep(.02)
                assert waiting>=2,'Both requests must actually contend for the complaint lock'
            finally:transaction.rollback()
            return [future.result(timeout=15) for future in futures]


def assert_single_acceptance(engine,station,complaint):
    with Session(engine) as db:
        docket=db.scalar(select(Docket).where(Docket.complaint_id==complaint))
        assert docket is not None and db.get(Complaint,complaint).status=='DOCKET_CREATED'
        assert db.scalar(select(func.count()).select_from(ComplaintDecision).where(ComplaintDecision.complaint_id==complaint))==1
        assert db.scalar(select(func.count()).select_from(DocketStatusHistory).where(DocketStatusHistory.docket_id==docket.id))==1
        assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id==station,IdentifierCounter.counter_type=='CAS'))==1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_id==docket.id,AuditLog.action=='docket.create'))==1
        assert set(db.scalars(select(Notification.event_type).where(Notification.complaint_id==complaint)))=={'complaint.accepted','docket.created'}
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.complaint_id==complaint))==2


def test_two_acceptances_create_one_docket_and_one_cas(race_database):
    station,actor,complaint=setup_case(race_database)
    operation=lambda db:ComplaintDecisionService(db).decide(actor,complaint,ComplaintDecisionRequest(decision='ACCEPTED'))
    assert sorted(contend(race_database,complaint,[operation,operation]))==[200,409]
    assert_single_acceptance(race_database,station,complaint)


def test_acceptance_racing_compatibility_endpoint_never_allocates_twice(race_database):
    station,actor,complaint=setup_case(race_database)
    accept=lambda db:ComplaintDecisionService(db).decide(actor,complaint,ComplaintDecisionRequest(decision='ACCEPTED'))
    ensure=lambda db:DocketService(db).open(actor,complaint)
    statuses=contend(race_database,complaint,[accept,ensure])
    assert statuses[0]==200 and statuses[1] in (200,409)
    assert_single_acceptance(race_database,station,complaint)


def test_distinct_complaints_receive_distinct_cas_under_parallel_acceptance(race_database):
    station,actor,first=setup_case(race_database)
    with Session(race_database) as db:
        original=db.get(Complaint,first)
        second=Complaint(reference_number='RACE-'+uuid.uuid4().hex,complainant_id=original.complainant_id,
            station_id=station,channel='ONLINE',status='SUBMITTED',crime_category='Theft',
            incident_description='Second statement',incident_location='Test',incident_province='Test')
        db.add(second);db.flush();second_id=second.id;db.commit()
    def accept(complaint):
        with Session(race_database) as db:
            db.execute(text('SET LOCAL ROLE saps_api'))
            return ComplaintDecisionService(db).decide(actor,complaint,ComplaintDecisionRequest(decision='ACCEPTED')).cas_number
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(accept,[first,second_id]))
    assert len(set(results))==2
    with Session(race_database) as db:
        assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id==station,IdentifierCounter.counter_type=='CAS'))==2
