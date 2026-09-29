from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.models import Role, UserRole
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.investigations.models import CaseAssignment
from app.modules.investigations.schemas import AssignmentRequest
from app.modules.investigations.service import InvestigationService
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Officer
from test_decisions_dockets import workflow_context, complaint_at_station, officer_account
from test_investigation_api import investigation, assign
from test_investigation_concurrency import race_database, race_case


def candidates(client, commander):
    result = client.get('/api/v1/stations/investigators', headers=commander)
    assert result.status_code == 200
    return {row['id'] for row in result.json()}


def test_busy_on_hold_unassigned_and_closed_availability(investigation):
    client, db, docket, commander, investigator, officer, _, other, station = investigation
    assert str(officer.id) in candidates(client, commander)
    assign(investigation)
    assert str(officer.id) not in candidates(client, commander)
    assert str(other.id) in candidates(client, commander)
    status_url = f'/api/v1/dockets/{docket}/status'
    assert client.post(status_url, headers=investigator, json={
        'expected_status': 'ACTIVE', 'status': 'ON_HOLD', 'reason': 'Waiting'}).status_code == 200
    assert str(officer.id) not in candidates(client, commander)
    assert client.post(f'/api/v1/dockets/{docket}/assignments', headers=commander, json={
        'investigating_officer_id': str(other.id), 'reason': 'Reassign'}).status_code == 201
    assert str(officer.id) in candidates(client, commander)
    assert str(other.id) not in candidates(client, commander)
    # Explicit unassignment releases an investigator even if the docket stays open.
    assert client.post(f'/api/v1/dockets/{docket}/assignments/end', headers=commander,
        json={'reason': 'Release investigator'}).status_code == 200
    assert str(other.id) in candidates(client, commander)
    assign(investigation)
    assert client.post(status_url, headers=investigator, json={
        'expected_status': 'ON_HOLD', 'status': 'CLOSED', 'reason': 'Completed'}).status_code == 200
    assert str(officer.id) in candidates(client, commander)


def test_concurrent_allocations_to_different_cases_have_one_winner(race_database, race_case):
    first_id, actor_id, officers = race_case
    target_id = officers[1]
    with Session(race_database, expire_on_commit=False) as db:
        source = db.get(Docket, first_id)
        original = db.get(Complaint, source.complaint_id)
        commander = db.scalar(select(Officer).where(Officer.user_id == actor_id))
        role = db.scalar(select(Role.id).where(Role.code == 'STATION_COMMANDER'))
        db.add(UserRole(user_id=actor_id, role_id=role))
        complaint = Complaint(reference_number='AVAIL-' + uuid.uuid4().hex, complainant_id=original.complainant_id,
            station_id=original.station_id, channel='ONLINE', status='ACCEPTED', crime_category='Theft',
            incident_description='Availability test', incident_location='Test', incident_province='Test')
        db.add(complaint); db.flush()
        decision = ComplaintDecision(complaint_id=complaint.id, decision='ACCEPTED', decided_by_officer_id=commander.id)
        db.add(decision); db.flush()
        second = Docket(complaint_id=complaint.id, cas_number='AVAIL-' + uuid.uuid4().hex,
            created_from_decision_id=decision.id, opened_by_officer_id=commander.id, status='APPROVED')
        db.add(second); db.commit()
        second_id = second.id
    gate = Barrier(2)
    def allocate(docket_id):
        with Session(race_database) as db:
            gate.wait(timeout=10)
            try:
                InvestigationService(db).assign(actor_id, docket_id, AssignmentRequest(
                    investigating_officer_id=target_id, reason='Concurrent allocation'))
                return 201
            except HTTPException as error:
                assert 'busy' in error.detail
                return error.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(allocate, (first_id, second_id))) == [201, 409]
    with Session(race_database) as db:
        rows = list(db.scalars(select(CaseAssignment).where(CaseAssignment.investigating_officer_id == target_id,
            CaseAssignment.unassigned_at.is_(None))))
        assert len(rows) == 1
