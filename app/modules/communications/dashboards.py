"""Aggregate from workflow tables; never maintain duplicate dashboard state."""
from sqlalchemy import select, func

from app.modules.alerts.models import Alert
from app.modules.authentication.security import utcnow
from app.modules.communications.common import ScopedService, transactional
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.investigations.models import CaseAssignment
from app.modules.refusals.models import RefusalEscalation


class DashboardService(ScopedService):
    @transactional
    def summary(self, user):
        station = self.management_scope(user)

        def counts(model):
            query = select(model.status, func.count()).select_from(model)
            if model in (Docket, RefusalEscalation):
                query = query.join(Complaint, Complaint.id == model.complaint_id)
                scope = Complaint.station_id
            else:
                scope = model.station_id
            if station is not None:
                query = query.where(scope == station)
            return dict(self.db.execute(query.group_by(model.status)).all())

        active = select(CaseAssignment.docket_id).where(CaseAssignment.unassigned_at.is_(None))
        unassigned = select(func.count()).select_from(Docket).join(Complaint).where(
            Docket.status.in_(['APPROVED', 'ACTIVE', 'ON_HOLD']), Docket.id.not_in(active))
        if station is not None:
            unassigned = unassigned.where(Complaint.station_id == station)
        result = {'scope': 'all' if station is None else 'station', 'station_id': station,
            'generated_at': utcnow(), 'complaints_by_status': counts(Complaint),
            'dockets_by_status': counts(Docket), 'alerts_by_status': counts(Alert),
            'escalations_by_status': counts(RefusalEscalation),
            'unassigned_open_dockets': self.db.scalar(unassigned)}
        self.audit(user, 'dashboard.view', 'dashboard', station_id=station)
        return result
