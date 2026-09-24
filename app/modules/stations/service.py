"""Station-scoped officer discovery for commander assignment workflows."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access.models import Role, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.stations.models import Officer
from app.modules.stations.schemas import InvestigatorOut


class StationOfficerService:
    def __init__(self, db: Session):
        self.db = db

    def investigators(self, user_id: uuid.UUID) -> list[InvestigatorOut]:
        try:
            commander_role = self.db.scalar(select(Role.id).join(UserRole).where(
                UserRole.user_id == user_id, Role.code == 'STATION_COMMANDER'))
            commander = self.db.scalar(select(Officer).where(
                Officer.user_id == user_id, Officer.is_active.is_(True)))
            if commander_role is None or commander is None:
                raise HTTPException(403, 'Active station commander profile required')
            rows = self.db.execute(select(Officer, User.username).join(
                User, User.id == Officer.user_id).join(
                UserRole, UserRole.user_id == User.id).join(
                Role, Role.id == UserRole.role_id).where(
                    Officer.station_id == commander.station_id,
                    Officer.is_active.is_(True), User.is_active.is_(True),
                    Role.code == 'INVESTIGATING_OFFICER').order_by(
                        Officer.rank, Officer.service_number)).all()
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action='officer.list_station_investigators', entity_type='station',
                                entity_id=commander.station_id, station_id=commander.station_id))
            result = [InvestigatorOut(id=officer.id, service_number=officer.service_number,
                                      rank=officer.rank, username=username)
                      for officer, username in rows]
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Station investigator list unavailable') from None
        except Exception:
            self.db.rollback()
            raise
