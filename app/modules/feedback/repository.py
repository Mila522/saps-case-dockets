"""Queries only. The feedback service owns commits and rollbacks."""
from sqlalchemy import select

from app.modules.authentication.repository import AuthRepository
from app.modules.communications.models import CaseFeedback
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.investigations.models import CaseAssignment
from app.modules.stations.models import Officer, Station


class FeedbackRepository:
    def __init__(self, db):
        self.db = db

    def permissions(self, user_id):
        return {p.code for p in AuthRepository(self.db).permissions(user_id)}

    def context(self, docket_id, *, lock=False):
        query = select(Docket, Complaint).join(Complaint, Docket.complaint_id == Complaint.id).where(Docket.id == docket_id)
        if lock:
            # Match C's docket-first locking; feedback does not mutate complaints.
            query = query.with_for_update(of=Docket).execution_options(populate_existing=True)
        return self.db.execute(query).first()

    def owner(self, complainant_id, user_id):
        return self.db.scalar(select(Complainant.id).where(Complainant.id == complainant_id, Complainant.user_id == user_id)) is not None

    def officer(self, user_id, *, lock=False):
        query = select(Officer).join(Station).where(Officer.user_id == user_id,
            Officer.is_active.is_(True), Station.is_active.is_(True))
        return self.db.scalar(query.with_for_update(of=Officer) if lock else query)

    def assigned(self, docket_id, officer_id, *, lock=False):
        query = select(CaseAssignment).where(CaseAssignment.docket_id == docket_id,
            CaseAssignment.investigating_officer_id == officer_id, CaseAssignment.unassigned_at.is_(None))
        return self.db.scalar(query.with_for_update() if lock else query) is not None

    def get(self, docket_id, feedback_id, complainant_id):
        return self.db.scalar(select(CaseFeedback).where(CaseFeedback.id == feedback_id,
            CaseFeedback.docket_id == docket_id, CaseFeedback.complainant_id == complainant_id))

    def superseded(self, feedback_id):
        return self.db.scalar(select(CaseFeedback.id).where(CaseFeedback.supersedes_feedback_id == feedback_id).limit(1)) is not None

    def list(self, docket_id, complainant_id, limit, offset):
        return self.db.scalars(select(CaseFeedback).where(CaseFeedback.docket_id == docket_id,
            CaseFeedback.complainant_id == complainant_id).order_by(CaseFeedback.published_at.desc(),
            CaseFeedback.id.desc()).limit(limit).offset(offset)).all()
