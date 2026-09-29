import uuid
from functools import wraps
from fastapi import HTTPException
from sqlalchemy import select, func, or_, update, delete
from sqlalchemy.exc import IntegrityError
from app.modules.access.models import User, Role, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.models import AuthSession
from app.modules.authentication.security import hash_password, utcnow
from app.modules.authentication.repository import AuthRepository
from app.modules.stations.models import Station, Officer
from app.modules.investigations.models import CaseAssignment
from app.modules.dockets.models import Docket
from .schemas import STAFF_ROLES


def transaction(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
            self.db.commit()
            return result
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(409, 'Username, email or service number is already in use') from None
        except Exception:
            self.db.rollback()
            raise
    return call


class AdminService:
    def __init__(self, db, actor):
        self.db, self.actor = db, actor

    def audit(self, action, entity='user', identity=None, before=None, after=None):
        self.db.add(AuditLog(actor_type='USER', actor_user_id=self.actor.id, action=action,
            entity_type=entity, entity_id=identity, old_values=before, new_values=after))

    def user_out(self, user):
        officer = self.db.scalar(select(Officer).where(Officer.user_id == user.id))
        roles = [r.code for r in AuthRepository(self.db).roles(user.id)]
        station = self.db.get(Station, officer.station_id) if officer else None
        return dict(id=user.id, username=user.username, email=user.email, phone_number=user.phone_number,
            is_active=user.is_active, is_verified=user.is_verified, roles=roles, updated_at=user.updated_at,
            station_id=officer.station_id if officer else None, station_name=station.name if station else None,
            service_number=officer.service_number if officer else None, rank=officer.rank if officer else None,
            editable=bool(officer and len(roles)==1 and roles[0] in STAFF_ROLES and user.id != self.actor.id))

    @transaction
    def users(self, q, role, station_id, active, limit, offset):
        query = select(User)
        if q:
            query = query.where(or_(User.username.icontains(q, autoescape=True), User.email.icontains(q, autoescape=True)))
        if role:
            query = query.where(User.id.in_(select(UserRole.user_id).join(Role).where(Role.code == role)))
        if station_id:
            query = query.where(User.id.in_(select(Officer.user_id).where(Officer.station_id == station_id)))
        if active is not None:
            query = query.where(User.is_active == active)
        total = self.db.scalar(select(func.count()).select_from(query.subquery()))
        result = [self.user_out(u) for u in self.db.scalars(query.order_by(User.username, User.id).limit(limit).offset(offset))]
        self.audit('admin.users.list')
        return dict(items=result, total=total)

    def station_role(self, data, *, allow_inactive=False):
        query = select(Station).where(Station.id==data.station_id)
        if not allow_inactive: query=query.where(Station.is_active.is_(True))
        station = self.db.scalar(query.with_for_update(read=True))
        role = self.db.scalar(select(Role).where(Role.code==data.role))
        if station is None:
            raise HTTPException(422, 'Select an active station')
        if role is None:
            raise HTTPException(409, 'Staff role is not configured')
        return role

    @transaction
    def create(self, data):
        role = self.station_role(data)
        user = User(username=data.username, email=str(data.email), password_hash=hash_password(data.password.get_secret_value()),
                    phone_number=data.phone_number, is_active=True, is_verified=False, mfa_enabled=False)
        self.db.add(user); self.db.flush()
        self.db.add_all([Officer(user_id=user.id, station_id=data.station_id, service_number=data.service_number,
                                rank=data.rank, is_active=True),
                         UserRole(user_id=user.id, role_id=role.id, assigned_by_user_id=self.actor.id)])
        self.audit('admin.staff.create', identity=user.id, after={'role':data.role,'station_id':str(data.station_id),'is_active':True})
        self.db.flush()
        return self.user_out(user)

    @transaction
    def change(self, identity, data):
        user = self.db.scalar(select(User).where(User.id==identity).with_for_update().execution_options(populate_existing=True))
        if user is None:
            raise HTTPException(404, 'User not found')
        roles = {r.code for r in AuthRepository(self.db).roles(user.id)}
        if identity == self.actor.id or len(roles)!=1 or not roles.issubset(STAFF_ROLES):
            raise HTTPException(403, 'Only single-role staff accounts can be managed here')
        officer = self.db.scalar(select(Officer).where(Officer.user_id==identity).with_for_update())
        if officer is None:
            raise HTTPException(409, 'Staff officer profile is missing')
        if user.updated_at != data.expected_updated_at:
            raise HTTPException(409, 'Account changed; reload before saving')
        # Same lock used by investigator assignment protects station/role changes.
        moving = officer.station_id!=data.station_id or roles!={data.role}
        if moving and self.db.scalar(select(CaseAssignment.id).join(Docket).where(
                CaseAssignment.investigating_officer_id==officer.id, CaseAssignment.unassigned_at.is_(None),
                Docket.status.notin_(['CLOSED','ARCHIVED'])).limit(1)):
            raise HTTPException(409, 'Reassign this officer’s open dockets through the commander before changing role or station')
        role = self.station_role(data, allow_inactive=not data.is_active and officer.station_id==data.station_id)
        before = {'role':next(iter(roles)), 'station_id':str(officer.station_id), 'is_active':user.is_active,
                  'rank':officer.rank, 'service_number':officer.service_number}
        if roles!={data.role}:
            self.db.execute(delete(UserRole).where(UserRole.user_id==identity))
            self.db.add(UserRole(user_id=identity, role_id=role.id, assigned_by_user_id=self.actor.id))
        officer.station_id, officer.rank, officer.service_number = data.station_id, data.rank, data.service_number
        officer.is_active = user.is_active = data.is_active
        user.phone_number, user.updated_at = data.phone_number, utcnow()
        self.db.execute(update(AuthSession).where(AuthSession.user_id==identity, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        self.audit('admin.staff.update', identity=identity, before=before,
            after={'role':data.role,'station_id':str(data.station_id),'is_active':data.is_active,
                   'rank':data.rank,'service_number':data.service_number})
        self.db.flush()
        return self.user_out(user)

    @staticmethod
    def audit_out(row, detail=False):
        result = {key:getattr(row,key) for key in ('id','actor_type','actor_user_id','action','entity_type','entity_id','station_id','occurred_at')}
        if detail:
            # No arbitrary case bodies, credentials, OTPs or free-text metadata.
            safe = {'role','station_id','is_active','rank','service_number'}
            for key in ('old_values','new_values'):
                values = getattr(row,key)
                result[key] = {k:v for k,v in values.items() if k in safe and isinstance(v,(str,bool,int,type(None)))} if row.action.startswith('admin.staff.') and isinstance(values,dict) else {}
            result['detail_notice'] = 'Sensitive case contents, authentication data and unrestricted metadata are withheld.'
        return result

    @transaction
    def audits(self, start, end, actor, action, entity_type, entity_id, limit, offset):
        if start and end and start>end:
            raise HTTPException(422, 'Start date must precede end date')
        query = select(AuditLog)
        for condition in [AuditLog.occurred_at>=start if start else None, AuditLog.occurred_at<=end if end else None,
                          AuditLog.actor_user_id==actor if actor else None, AuditLog.action==action if action else None,
                          AuditLog.entity_type==entity_type if entity_type else None, AuditLog.entity_id==entity_id if entity_id else None]:
            if condition is not None: query=query.where(condition)
        total=self.db.scalar(select(func.count()).select_from(query.subquery()))
        rows=self.db.scalars(query.order_by(AuditLog.occurred_at.desc(),AuditLog.id.desc()).limit(limit).offset(offset))
        result=dict(items=[self.audit_out(r) for r in rows],total=total)
        self.audit('admin.audit.list',entity='audit_log')
        return result

    @transaction
    def audit_detail(self, identity):
        row=self.db.get(AuditLog,identity)
        if row is None: raise HTTPException(404,'Audit entry not found')
        result=self.audit_out(row,True)
        self.audit('admin.audit.view',entity='audit_log',identity=identity)
        return result
