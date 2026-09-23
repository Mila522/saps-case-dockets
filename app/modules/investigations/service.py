"""Collaborator C scope, transactions, assignments and investigation workflow."""
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.models import User, Role, UserRole, Permission, RolePermission
from app.modules.audit.models import AuditLog
from app.modules.authentication.security import utcnow
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.dockets.schemas import DocketOut
from app.modules.investigations.models import CaseAssignment, DocketStatusHistory, InvestigationNote
from app.modules.investigations.schemas import AssignmentOut, NoteOut
from app.modules.stations.models import Officer, Station


def transactional(method):
    """Commit domain changes and redacted audit together, including sensitive reads."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        self._commit_started = False
        try:
            result = method(self, *args, **kwargs)
            self._commit_started = True
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Investigation service unavailable') from None
        except Exception:
            self.db.rollback()
            raise
    return wrapped


class InvestigationService:
    def __init__(self, db):
        self.db = db

    def officer(self, user_id, role):
        row = self.db.scalar(select(Officer).join(User, User.id == Officer.user_id)
            .join(Station, Station.id == Officer.station_id).where(
                Officer.user_id == user_id, Officer.is_active.is_(True),
                User.is_active.is_(True), Station.is_active.is_(True)))
        has_role = self.db.scalar(select(UserRole.user_id).join(Role).where(
            UserRole.user_id == user_id, Role.code == role))
        if row is None or has_role is None:
            raise HTTPException(403, 'Active officer and required role needed')
        return row

    def scope(self, user_id, docket_id, *, commander=False, writable=False):
        officer = self.officer(user_id, 'STATION_COMMANDER' if commander else 'INVESTIGATING_OFFICER')
        # All C writes lock the docket first. Reassignment cannot race a note,
        # status update or evidence operation authorized against an old assignment.
        docket = self.db.scalar(select(Docket).join(Complaint).where(
            Docket.id == docket_id, Complaint.station_id == officer.station_id)
            .with_for_update(of=Docket))
        if docket is None:
            raise HTTPException(404, 'Docket not found')
        if not commander:
            assignment = self.db.scalar(select(CaseAssignment.id).where(
                CaseAssignment.docket_id == docket.id,
                CaseAssignment.investigating_officer_id == officer.id,
                CaseAssignment.unassigned_at.is_(None)))
            if assignment is None:
                raise HTTPException(404, 'Docket not found')
        if writable and docket.status not in ('APPROVED', 'ACTIVE', 'ON_HOLD'):
            raise HTTPException(409, 'Docket is not open for investigation')
        return officer, docket

    def audit(self, user_id, station_id, action, entity_type, entity_id=None):
        self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id, station_id=station_id,
                            action=action, entity_type=entity_type, entity_id=entity_id))

    def transition(self, docket, user_id, status, reason):
        self.db.add(DocketStatusHistory(docket_id=docket.id, from_status=docket.status,
            to_status=status, changed_by_user_id=user_id, change_reason=reason, changed_at=utcnow()))
        docket.status = status
        if status == 'CLOSED':
            docket.closed_at, docket.closure_reason = utcnow(), reason

    @transactional
    def assign(self, user_id, docket_id, data):
        commander, docket = self.scope(user_id, docket_id, commander=True, writable=True)
        target = self.db.scalar(select(Officer).where(Officer.id == data.investigating_officer_id))
        if target is None or target.station_id != commander.station_id:
            raise HTTPException(422, 'Select an active investigator at this station')
        self.officer(target.user_id, 'INVESTIGATING_OFFICER')
        current = self.db.scalar(select(CaseAssignment).where(
            CaseAssignment.docket_id == docket.id, CaseAssignment.unassigned_at.is_(None)))
        if current and current.investigating_officer_id == target.id:
            raise HTTPException(409, 'Investigator is already assigned')
        now = utcnow()
        if current:
            current.unassigned_at, current.unassignment_reason = now, data.reason
            self.db.flush()  # Release the partial unique key before inserting replacement.
        row = CaseAssignment(docket_id=docket.id, investigating_officer_id=target.id,
            assigned_by_officer_id=commander.id, assigned_at=now, assignment_reason=data.reason)
        self.db.add(row)
        if docket.status == 'APPROVED':
            self.transition(docket, user_id, 'ACTIVE', 'Investigator assigned')
        self.db.flush()
        self.audit(user_id, commander.station_id, 'docket.assign', 'case_assignment', row.id)
        return AssignmentOut.model_validate(row)

    @transactional
    def unassign(self, user_id, docket_id, data):
        officer, docket = self.scope(user_id, docket_id, commander=True, writable=True)
        row = self.db.scalar(select(CaseAssignment).where(
            CaseAssignment.docket_id == docket.id, CaseAssignment.unassigned_at.is_(None)))
        if row is None:
            raise HTTPException(409, 'No active assignment')
        row.unassigned_at, row.unassignment_reason = utcnow(), data.reason
        self.audit(user_id, officer.station_id, 'docket.unassign', 'case_assignment', row.id)
        return AssignmentOut.model_validate(row)

    @transactional
    def assignments(self, user_id, docket_id, limit, offset):
        officer, docket = self.scope(user_id, docket_id, commander=True)
        rows = self.db.scalars(select(CaseAssignment).where(CaseAssignment.docket_id == docket.id)
            .order_by(CaseAssignment.assigned_at, CaseAssignment.id).limit(limit).offset(offset))
        result = [AssignmentOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'docket.assignments.view', 'docket', docket.id)
        return result

    @transactional
    def mine(self, user_id, limit, offset):
        officer = self.officer(user_id, 'INVESTIGATING_OFFICER')
        rows = self.db.scalars(select(Docket).join(Complaint).join(CaseAssignment).where(
            Complaint.station_id == officer.station_id,
            CaseAssignment.investigating_officer_id == officer.id,
            CaseAssignment.unassigned_at.is_(None)).order_by(Docket.opened_at, Docket.id)
            .limit(limit).offset(offset))
        result = [DocketOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'docket.list_assigned', 'docket')
        return result

    @transactional
    def get(self, user_id, docket_id):
        officer, docket = self.scope(user_id, docket_id)
        self.audit(user_id, officer.station_id, 'docket.view_assigned', 'docket', docket.id)
        return DocketOut.model_validate(docket)

    @transactional
    def add_note(self, user_id, docket_id, data):
        officer, docket = self.scope(user_id, docket_id, writable=True)
        row = InvestigationNote(docket_id=docket.id, author_officer_id=officer.id,
                                created_at=utcnow(), **data.model_dump())
        self.db.add(row)
        self.db.flush()
        self.audit(user_id, officer.station_id, 'case.add_note', 'investigation_note', row.id)
        return NoteOut.model_validate(row)

    @transactional
    def notes(self, user_id, docket_id, limit, offset):
        officer, docket = self.scope(user_id, docket_id)
        rows = self.db.scalars(select(InvestigationNote).where(InvestigationNote.docket_id == docket.id)
            .order_by(InvestigationNote.created_at, InvestigationNote.id).limit(limit).offset(offset))
        result = [NoteOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'case.notes.view', 'docket', docket.id)
        return result

    @transactional
    def update_status(self, user_id, docket_id, data):
        officer, docket = self.scope(user_id, docket_id, writable=True)
        if data.status == 'CLOSED':
            allowed = self.db.scalar(select(Permission.id).join(RolePermission).join(UserRole,
                UserRole.role_id == RolePermission.role_id).where(
                    UserRole.user_id == user_id, Permission.code == 'case.close'))
            if allowed is None:
                raise HTTPException(403, 'Case closure permission required')
        transitions = {'ACTIVE': {'ON_HOLD', 'CLOSED'}, 'ON_HOLD': {'ACTIVE', 'CLOSED'}}
        if data.status not in transitions.get(docket.status, set()):
            raise HTTPException(409, 'Invalid investigation status transition')
        self.transition(docket, user_id, data.status, data.reason)
        self.audit(user_id, officer.station_id, 'case.update_status', 'docket', docket.id)
        return DocketOut.model_validate(docket)
