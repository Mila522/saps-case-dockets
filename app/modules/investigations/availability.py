"""An investigator is available only when no unfinished docket is assigned."""
from sqlalchemy import select

from app.modules.dockets.models import Docket
from app.modules.investigations.models import CaseAssignment


def busy_assignment(officer_id):
    return select(CaseAssignment.id).join(Docket, Docket.id == CaseAssignment.docket_id).where(
        CaseAssignment.investigating_officer_id == officer_id,
        CaseAssignment.unassigned_at.is_(None),
        Docket.status.not_in(('CLOSED', 'ARCHIVED')),
    )
