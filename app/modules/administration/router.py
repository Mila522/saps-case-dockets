import uuid
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.authentication.dependencies import require_role, require_permission
from app.modules.access.models import Role
from app.modules.stations.models import Station
from .schemas import StaffCreate, StaffUpdate, STAFF_ROLES
from .service import AdminService

router=APIRouter(prefix='/admin', tags=['System administration'], dependencies=[Depends(require_role('SYSTEM_ADMINISTRATOR'))])


def accounts(db: Session=Depends(get_db), user=Depends(require_permission('user.manage'))):
    return AdminService(db,user)


def audit_service(db: Session=Depends(get_db), user=Depends(require_permission('audit.view_all'))):
    return AdminService(db,user)


@router.get('/options')
def options(service=Depends(accounts)):
    stations=[dict(id=s.id,name=s.name,is_active=s.is_active) for s in service.db.scalars(select(Station).order_by(Station.name))]
    roles=[dict(code=r.code,name=r.name) for r in service.db.scalars(select(Role).order_by(Role.name))]
    return dict(stations=stations,roles=roles,staff_roles=list(STAFF_ROLES))


@router.get('/users')
def users(q: str|None=Query(None,max_length=254), role: str|None=Query(None,max_length=100), station_id: uuid.UUID|None=None,
          active: bool|None=None, limit: int=Query(25,ge=1,le=100), offset: int=Query(0,ge=0), service=Depends(accounts)):
    return service.users(q,role,station_id,active,limit,offset)


@router.post('/users',status_code=201)
def create(data: StaffCreate, service=Depends(accounts)):
    return service.create(data)


@router.patch('/users/{identity}')
def change(identity: uuid.UUID, data: StaffUpdate, service=Depends(accounts)):
    return service.change(identity,data)


@router.get('/audit')
def audits(start: AwareDatetime|None=None,end: AwareDatetime|None=None,actor: uuid.UUID|None=None,
           action: str|None=Query(None,max_length=100),entity_type: str|None=Query(None,max_length=100),entity_id: uuid.UUID|None=None,
           limit: int=Query(25,ge=1,le=100),offset: int=Query(0,ge=0),service=Depends(audit_service)):
    return service.audits(start,end,actor,action,entity_type,entity_id,limit,offset)


@router.get('/audit/{identity}')
def audit_detail(identity: uuid.UUID,service=Depends(audit_service)):
    return service.audit_detail(identity)
