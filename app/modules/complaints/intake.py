"""Explicit walk-in intake and authorised, versioned digital-docket material."""
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.models import Role, UserRole
from app.modules.authentication.repository import AuthRepository
from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint, ComplaintStatement, Witness, WitnessStatement
from app.modules.complaints.schemas import ComplaintMaterialOut, StatementOut, WitnessOut
from app.modules.complaints.service import StationComplaintService
from app.modules.dockets.models import Docket
from app.modules.evidence.service import EvidenceService
from app.modules.evidence.models import EvidenceItem
from app.modules.investigations.service import InvestigationService
from app.modules.stations.models import Station
from app.modules.system.service import allocate_complaint_reference
from app.modules.communications.notifications import notify_complainant


def atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Complaint intake service unavailable') from None
        except Exception:
            self.db.rollback()
            raise
    return wrapped


class ComplaintIntakeService(StationComplaintService):
    def audit(self, actor, complaint, action):
        self.db.add(AuditLog(actor_type='USER', actor_user_id=actor, action=action,
            entity_type='complaint', entity_id=complaint.id, station_id=complaint.station_id))

    @atomic
    def register(self, user_id, data):
        officer = self._officer(user_id, {'CHARGE_OFFICER'})
        station = self.db.scalar(select(Station).where(Station.id == officer.station_id,
            Station.is_active.is_(True)).with_for_update(read=True))
        if station is None:
            raise HTTPException(403, 'Active receiving station required')
        now = utcnow()
        if data.incident_occurred_at and data.incident_occurred_at > now:
            raise HTTPException(422, 'Incident date cannot be in the future')
        # This workflow creates a new, explicitly unlinked walk-in profile. Never
        # resolve an online account by phone/email or accept arbitrary person IDs.
        person = Complainant(**data.complainant.model_dump(), user_id=None)
        self.db.add(person)
        self.db.flush()
        complaint = Complaint(**data.model_dump(exclude={'complainant', 'details_confirmed_with_complainant', 'witnesses'}),
            complainant_id=person.id, registered_by_officer_id=officer.id, station_id=station.id,
            reference_number=allocate_complaint_reference(self.db, station, now),
            channel='IN_STATION', status='SUBMITTED', submitted_at=now)
        self.db.add(complaint)
        self.db.flush()
        self.db.add(ComplaintStatement(complaint_id=complaint.id, statement_text=data.incident_description,
            recorded_by_officer_id=officer.id, statement_version=1, is_current=True))
        for witness in data.witnesses:
            self._witness(complaint, officer.id, witness)
        self.audit(user_id, complaint, 'complaint.register_in_station')
        notify_complainant(self.db, complaint, 'complaint.registered', actor_user_id=user_id)
        return self.station_out(complaint)

    def scope(self, actor, complaint_id, *, write=False, initial_evidence=False):
        permissions = {permission.code for permission in AuthRepository(self.db).permissions(actor)}
        roles = set(self.db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == actor)))
        # Serialize acceptance and material changes on the complaint. C mutation
        # services lock only the docket; neither statement body is updated in place.
        complaint = self.db.scalar(select(Complaint).where(Complaint.id == complaint_id)
            .with_for_update().execution_options(populate_existing=True))
        if complaint is None:
            raise HTTPException(404, 'Complaint not found')
        if 'CHARGE_OFFICER' in roles or ('STATION_COMMANDER' in roles and not write):
            permission = 'complaint.register' if write else 'complaint.view_station'
            if permission not in permissions:
                raise HTTPException(403, 'Insufficient permission')
            officer = self._officer(actor, {'CHARGE_OFFICER'} if write else {'CHARGE_OFFICER', 'STATION_COMMANDER'})
            if complaint.station_id != officer.station_id:
                raise HTTPException(404, 'Complaint not found')
            docket = self.db.scalar(select(Docket).where(Docket.complaint_id == complaint.id)
                .with_for_update().execution_options(populate_existing=True))
            can_append = complaint.status in {'SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'DOCKET_CREATED'} and (
                docket is None or docket.status == 'PENDING_APPROVAL') and 'complaint.register' in permissions and 'CHARGE_OFFICER' in roles
            if write and not can_append:
                raise HTTPException(409, 'Station intake is closed; the assigned investigator maintains this docket')
            if initial_evidence and docket is None:
                raise HTTPException(409, 'Accept the complaint before registering initial evidence')
            return complaint, officer, docket, can_append
        if 'INVESTIGATING_OFFICER' in roles and not initial_evidence:
            if ('case.add_note' if write else 'docket.view_assigned') not in permissions:
                raise HTTPException(403, 'Insufficient permission')
            docket = self.db.scalar(select(Docket).where(Docket.complaint_id == complaint.id))
            if docket is None:
                raise HTTPException(404, 'Complaint not found')
            officer, docket = InvestigationService(self.db).scope(actor, docket.id, writable=write)
            return complaint, officer, docket, docket.status in {'APPROVED', 'ACTIVE', 'ON_HOLD'} and 'case.add_note' in permissions
        raise HTTPException(403, 'Authorised case officer required')

    def _witness(self, complaint, officer_id, data):
        witness = Witness(complaint_id=complaint.id, **data.model_dump(exclude={'statement_text'}))
        self.db.add(witness)
        self.db.flush()
        if data.statement_text:
            self.db.add(WitnessStatement(witness_id=witness.id, statement_text=data.statement_text,
                recorded_by_officer_id=officer_id, statement_version=1, is_current=True))
            self.db.flush()
        return witness

    def witness_out(self, witness):
        statements = self.db.scalars(select(WitnessStatement).where(WitnessStatement.witness_id == witness.id)
            .order_by(WitnessStatement.statement_version)).all()
        return WitnessOut(id=witness.id, first_name=witness.first_name, last_name=witness.last_name,
            phone_number=witness.phone_number, email=witness.email, address=witness.address,
            statements=[StatementOut.model_validate(row) for row in statements])

    @atomic
    def materials(self, actor, complaint_id):
        complaint, _, docket, can_append = self.scope(actor, complaint_id)
        statements = self.db.scalars(select(ComplaintStatement).where(ComplaintStatement.complaint_id == complaint.id)
            .order_by(ComplaintStatement.statement_version)).all()
        witnesses = self.db.scalars(select(Witness).where(Witness.complaint_id == complaint.id)
            .order_by(Witness.created_at, Witness.id)).all()
        self.audit(actor, complaint, 'complaint.materials.view')
        return ComplaintMaterialOut(complaint_id=complaint.id, can_append=can_append,
            initial_evidence=[EvidenceService(self.db).evidence_out(item) for item in self.db.scalars(
                select(EvidenceItem).where(EvidenceItem.docket_id == docket.id).order_by(EvidenceItem.registered_at))]
                if docket and docket.status == 'PENDING_APPROVAL' else [],
            statements=[StatementOut.model_validate(row) for row in statements],
            witnesses=[self.witness_out(row) for row in witnesses])

    @atomic
    def add_witness(self, actor, complaint_id, data):
        complaint, officer, _, _ = self.scope(actor, complaint_id, write=True)
        row = self._witness(complaint, officer.id, data)
        self.audit(actor, complaint, 'complaint.witness.record')
        return self.witness_out(row)

    @atomic
    def append_statement(self, actor, complaint_id, data, witness_id=None):
        complaint, officer, _, _ = self.scope(actor, complaint_id, write=True)
        if witness_id:
            witness = self.db.scalar(select(Witness).where(Witness.id == witness_id, Witness.complaint_id == complaint.id))
            if witness is None:
                raise HTTPException(404, 'Witness not found')
        model = WitnessStatement if witness_id else ComplaintStatement
        key = 'witness_id' if witness_id else 'complaint_id'
        parent_id = witness_id or complaint.id
        previous = self.db.scalar(select(model).where(getattr(model, key) == parent_id)
            .order_by(model.statement_version.desc()).limit(1).execution_options(populate_existing=True))
        version = previous.statement_version if previous else 0
        if data.expected_version != version:
            raise HTTPException(409, 'Statement version changed; reload before appending a correction')
        if previous:
            previous.is_current = False
            self.db.flush()
        row = model(**{key:parent_id}, statement_text=data.statement_text, recorded_by_officer_id=officer.id,
            statement_version=version + 1, is_current=True)
        self.db.add(row)
        self.db.flush()
        self.audit(actor, complaint, 'complaint.statement.append' if not witness_id else 'witness.statement.append')
        return StatementOut.model_validate(row)

    @atomic
    def initial_evidence(self, actor, complaint_id, data):
        complaint, officer, docket, _ = self.scope(actor, complaint_id, write=True, initial_evidence=True)
        # Use C's number allocation, validation, initial custody and audit, while
        # preserving the investigator-only scope of all existing C endpoints.
        return EvidenceService(self.db)._register_record(actor, officer, docket, data)
