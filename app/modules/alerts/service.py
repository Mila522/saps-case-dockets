"""Scoped operational alerts with explicit policy settings and serialized evaluation."""
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.alerts.models import Alert
from app.modules.authentication.security import utcnow
from app.modules.communications.common import ScopedService, transactional
from app.modules.communications.schemas import AlertOut
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.investigations.models import InvestigationNote, DocketStatusHistory, CaseAssignment
from app.modules.evidence.models import EvidenceItem
from app.modules.communications.models import CaseFeedback
from app.modules.refusals.models import ComplaintDecision, RefusalReason, RefusalEscalation


class AlertService(ScopedService):
    @transactional
    def list(self, user, limit, offset, status=None):
        self.require(user, 'alert.view')
        station = self.management_scope(user)
        query = select(Alert)
        if station is not None:
            query = query.where(Alert.station_id == station)
        if status:
            query = query.where(Alert.status == status)
        result = [AlertOut.model_validate(row) for row in self.db.scalars(query
            .order_by(Alert.detected_at.desc(), Alert.id.desc()).limit(limit).offset(offset))]
        self.audit(user, 'alert.list', 'alert', station_id=station)
        return result

    @transactional
    def transition(self, user, alert_id, data):
        self.require(user, 'alert.view')
        station = self.management_scope(user, write=True)
        row = self.db.scalar(select(Alert).where(Alert.id == alert_id,
            Alert.station_id == station).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise HTTPException(404, 'Alert not found')
        if row.assigned_to_user_id not in (None, user.id):
            raise HTTPException(403, 'Alert is assigned to another user')
        if row.status != data.expected_status:
            raise HTTPException(409, 'Alert changed; refresh before retrying')
        allowed = {'OPEN': {'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED'},
                   'ACKNOWLEDGED': {'RESOLVED', 'DISMISSED'}}
        if data.status not in allowed.get(row.status, set()):
            raise HTTPException(409, 'Invalid alert transition')
        row.status, row.resolution_notes = data.status, data.notes
        row.assigned_to_user_id = user.id
        if data.status == 'ACKNOWLEDGED':
            row.acknowledged_by_user_id, row.acknowledged_at = user.id, utcnow()
        else:
            row.resolved_by_user_id, row.resolved_at = user.id, utcnow()
        self.audit(user, 'alert.' + data.status.lower(), 'alert', row.id, station)
        return AlertOut.model_validate(row)

    @transactional
    def evaluate(self, user):
        self.require(user, 'alert.view')
        station_id = self.management_scope(user, write=True)
        # A transaction advisory lock avoids reversing the complaint -> station
        # row-lock order used by B's docket creation. It is not an identifier.
        lock_key = int.from_bytes(sha256(f'alerts:{station_id}'.encode()).digest()[:8], signed=True)
        self.db.scalar(select(func.pg_advisory_xact_lock(lock_key)))
        now, created = utcnow(), []

        def emit(kind, complaint, docket=None, due=None, source=None):
            # The source id identifies the episode, including resolved/dismissed
            # alerts. A subsequent activity/deadline can start a new episode.
            marker = str(source or (docket.id if docket else complaint.id))
            title = kind.replace('_', ' ').title()
            description = f'{title}. Source: {marker}'
            existing = self.db.scalar(select(Alert.id).where(Alert.station_id == station_id,
                Alert.alert_type == kind, Alert.complaint_id == complaint.id,
                Alert.description == description))
            if existing:
                return
            row = Alert(id=uuid4(), station_id=station_id, complaint_id=complaint.id,
                docket_id=docket.id if docket else None, alert_type=kind,
                severity='HIGH' if kind == 'NON_COMPLIANT_REFUSAL' else 'MEDIUM',
                status='OPEN', title=title, description=description, detected_at=now, due_at=due)
            self.db.add(row)
            self.db.flush()
            self.audit(user, 'alert.detected', 'alert', row.id, station_id)
            created.append(row.id)

        for complaint, decision in self.db.execute(select(Complaint, ComplaintDecision)
            .join(ComplaintDecision, ComplaintDecision.complaint_id == Complaint.id)
            .join(RefusalReason, RefusalReason.id == ComplaintDecision.refusal_reason_id)
            .where(Complaint.station_id == station_id, ComplaintDecision.decision == 'REFUSED',
                RefusalReason.is_non_compliant.is_(True))):
            emit('NON_COMPLIANT_REFUSAL', complaint, source=decision.id)
        for complaint, escalation in self.db.execute(select(Complaint, RefusalEscalation)
            .join(RefusalEscalation, RefusalEscalation.complaint_id == Complaint.id)
            .where(Complaint.station_id == station_id, RefusalEscalation.status == 'OPEN',
                RefusalEscalation.escalated_at <= now - timedelta(hours=settings.alert_escalation_hours))):
            emit('UNACKNOWLEDGED_ESCALATION', complaint, source=escalation.id,
                due=escalation.escalated_at + timedelta(hours=settings.alert_escalation_hours))
        for complaint, docket in self.db.execute(select(Complaint, Docket)
            .join(Docket, Docket.complaint_id == Complaint.id).where(Complaint.station_id == station_id,
                Docket.status.in_(['PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD']))):
            if docket.status == 'PENDING_APPROVAL':
                due = docket.opened_at + timedelta(hours=settings.alert_docket_approval_hours)
                if due <= now:
                    emit('OVERDUE_DOCKET', complaint, docket, due)
                continue
            activity = [docket.opened_at, docket.updated_at]
            for model, column in ((InvestigationNote, InvestigationNote.created_at),
                    (DocketStatusHistory, DocketStatusHistory.changed_at),
                    (CaseAssignment, CaseAssignment.assigned_at),
                    (EvidenceItem, EvidenceItem.updated_at), (CaseFeedback, CaseFeedback.published_at)):
                latest = self.db.scalar(select(func.max(column)).where(model.docket_id == docket.id))
                if latest:
                    activity.append(latest)
            last = max(activity)
            due = last + timedelta(days=settings.alert_inactivity_days)
            if due <= now:
                emit('CASE_INACTIVITY', complaint, docket, due, source=f'{docket.id}:{last.isoformat()}')
        self.audit(user, 'alert.evaluate', 'alert', station_id=station_id)
        return {'created': len(created), 'alert_ids': created}
