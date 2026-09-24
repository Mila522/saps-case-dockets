"""Shared D authorization and transaction boundary."""
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.audit.models import AuditLog
from app.modules.authentication.repository import AuthRepository
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.investigations.models import CaseAssignment
from app.modules.stations.models import Officer, Station


def transactional(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Communications service unavailable') from None
        except Exception:
            self.db.rollback()
            raise
    return wrapped


class ScopedService:
    def __init__(self, db):
        self.db = db

    def permissions(self, user):
        if not user.is_active:
            raise HTTPException(403, 'Inactive user')
        return {p.code for p in AuthRepository(self.db).permissions(user.id)}

    def require(self, user, permission):
        if permission not in self.permissions(user):
            raise HTTPException(403, 'Insufficient permission')

    def officer(self, user):
        return self.db.scalar(select(Officer).join(Station).where(
            Officer.user_id == user.id, Officer.is_active.is_(True), Station.is_active.is_(True)))

    def management_scope(self, user, *, write=False):
        permissions = self.permissions(user)
        roles = {r.code for r in AuthRepository(self.db).roles(user.id)}
        if not write and 'dashboard.view_all' in permissions and roles.intersection(
                {'SAPS_MANAGEMENT', 'SYSTEM_ADMINISTRATOR', 'NCC_OFFICER'}):
            return None
        required = 'docket.assign' if write else 'dashboard.view_station'
        officer = self.officer(user)
        if 'STATION_COMMANDER' not in roles or required not in permissions or officer is None:
            raise HTTPException(403, 'Station commander access required')
        return officer.station_id

    def complaint_scope(self, user, complaint_id):
        self.require(user, 'confirmation.download')
        row = self.db.get(Complaint, complaint_id)
        if row is None:
            raise HTTPException(404, 'Complaint not found')
        permissions = self.permissions(user)
        owner = self.db.scalar(select(Complainant.id).where(
            Complainant.id == row.complainant_id, Complainant.user_id == user.id))
        if owner and permissions.intersection({'complaint.view_own', 'case.track_own'}):
            return row
        officer = self.officer(user)
        if officer and officer.station_id == row.station_id:
            if 'complaint.view_station' in permissions:
                return row
            if 'docket.view_assigned' in permissions and self.db.scalar(
                select(CaseAssignment.id).join(Docket).where(Docket.complaint_id == row.id,
                    CaseAssignment.investigating_officer_id == officer.id,
                    CaseAssignment.unassigned_at.is_(None))):
                return row
        raise HTTPException(404, 'Complaint not found')

    def audit(self, user, action, entity_type, entity_id=None, station_id=None):
        self.db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action=action,
            entity_type=entity_type, entity_id=entity_id, station_id=station_id))
