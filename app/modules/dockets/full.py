"""Complete case record for authorized station staff and assigned investigators."""
from sqlalchemy import select, and_, or_
from fastapi import HTTPException
from app.modules.audit.models import AuditLog
from app.modules.complaints.intake import ComplaintIntakeService
from app.modules.complaints.models import ComplaintUpload
from app.modules.dockets.models import Docket
from app.modules.communications.models import Notification
from app.modules.stations.models import Officer, Station
from app.modules.access.models import User
from app.modules.investigations.service import InvestigationService, transactional
from app.modules.complaints.models import Complaint, ComplaintStatement, Witness, WitnessStatement
from app.modules.complainants.models import Complainant
from app.modules.dockets.models import DocketApproval
from app.modules.refusals.models import ComplaintDecision, RefusalEscalation, RefusalReason
from app.modules.investigations.models import CaseAssignment, DocketStatusHistory, InvestigationNote
from app.modules.evidence.models import EvidenceItem, EvidenceFile, EvidenceCustodyEvent
from app.modules.communications.models import CaseFeedback, OfficialDocument


def record(row, db):
    # Explicitly omit encrypted identity and private storage identifiers.
    result = {c.name: getattr(row, c.name) for c in row.__table__.columns
        if c.name not in {'storage_key', 'identity_number_encrypted', 'identity_number_hash', 'destination_encrypted'}}
    for key, value in list(result.items()):
        if value and key == 'station_id':
            station = db.get(Station, value)
            if station:
                result['receiving_station'] = station.name
                result['station_address'] = ', '.join(v for v in (station.address_line_1, station.address_line_2, station.city, station.province) if v)
        elif value and key == 'refusal_reason_id':
            reason = db.get(RefusalReason, value)
            if reason:
                result['refusal_reason'] = reason.name
        elif value and key.endswith('officer_id'):
            officer = db.get(Officer, value)
            if officer:
                user = db.get(User, officer.user_id)
                result[key[:-3]] = f'{officer.rank} {user.username} ({officer.service_number})'
        elif value and key.endswith('user_id'):
            user = db.get(User, value)
            if user:
                result[key[:-3]] = user.username
    return result



class FullDocketService(InvestigationService):
    def read_scope(self, actor, docket_id):
        docket = self.db.get(Docket, docket_id)
        if docket is None:
            raise HTTPException(404, 'Docket not found')
        complaint, officer, docket, _ = ComplaintIntakeService(self.db).scope(actor, docket.complaint_id)
        return officer, docket

    @transactional
    def get(self, actor, docket_id):
        officer, docket = self.read_scope(actor, docket_id)
        complaint = self.db.get(Complaint, docket.complaint_id)
        self.audit(actor, officer.station_id, 'docket.full.view', 'docket', docket.id)
        return self.assemble(complaint, docket)

    @transactional
    def complaint(self, actor, complaint_id):
        complaint, officer, docket, _ = ComplaintIntakeService(self.db).scope(actor, complaint_id)
        self.audit(actor, officer.station_id, 'complaint.dossier.view', 'complaint', complaint.id)
        return self.assemble(complaint, docket)

    def assemble(self, complaint, docket):
        docket_id = docket.id if docket else None
        def rows(model, condition):
            # Histories follow their sequence or event time, never random UUID order.
            order = next((getattr(model, key) for key in ('statement_version', 'decision_sequence', 'file_version',
                'assigned_at', 'changed_at', 'occurred_at', 'published_at', 'registered_at', 'generated_at', 'uploaded_at', 'created_at')
                if hasattr(model, key)), model.id)
            return [record(r, self.db) for r in self.db.scalars(select(model).where(condition).order_by(order, model.id))]
        witnesses = rows(Witness, Witness.complaint_id == complaint.id)
        for witness in witnesses:
            witness['statements'] = rows(WitnessStatement, WitnessStatement.witness_id == witness['id'])
        evidence = rows(EvidenceItem, EvidenceItem.docket_id == docket_id)
        for item in evidence:
            item['files'] = rows(EvidenceFile, EvidenceFile.evidence_item_id == item['id'])
            item['custody_history'] = rows(EvidenceCustodyEvent, EvidenceCustodyEvent.evidence_item_id == item['id'])
        result = {'docket': record(docket, self.db) if docket else None, 'complaint': record(complaint, self.db),
            'complainant': record(self.db.get(Complainant, complaint.complainant_id), self.db),
            'statements': rows(ComplaintStatement, ComplaintStatement.complaint_id == complaint.id),
            'witnesses': witnesses, 'evidence': evidence,
            'decisions': rows(ComplaintDecision, ComplaintDecision.complaint_id == complaint.id),
            'refusal_escalations': rows(RefusalEscalation, RefusalEscalation.complaint_id == complaint.id),
            'approvals': rows(DocketApproval, DocketApproval.docket_id == docket_id),
            'assignments': rows(CaseAssignment, CaseAssignment.docket_id == docket_id),
            'status_history': rows(DocketStatusHistory, DocketStatusHistory.docket_id == docket_id),
            'investigation_notes': rows(InvestigationNote, InvestigationNote.docket_id == docket_id),
            'official_feedback': rows(CaseFeedback, CaseFeedback.docket_id == docket_id),
            'documents': rows(OfficialDocument, OfficialDocument.complaint_id == complaint.id),
            'complainant_uploads': rows(ComplaintUpload, ComplaintUpload.complaint_id == complaint.id)}
        notifications = list(self.db.scalars(select(Notification).where(Notification.complaint_id == complaint.id)
            .order_by(Notification.created_at, Notification.id)))
        result['communications'] = [{key: getattr(row, key) for key in
            ('id', 'channel', 'event_type', 'subject', 'message', 'status', 'created_at')} for row in notifications]
        result['activity_trail'] = self.activity(complaint, docket, result)
        return result

    def activity(self, complaint, docket, result):
        entities = {'complaint': [complaint.id], 'docket': [docket.id] if docket else [],
            'witness': [r['id'] for r in result['witnesses']],
            'complaint_statement': [r['id'] for r in result['statements']],
            'witness_statement': [r['id'] for w in result['witnesses'] for r in w['statements']],
            'evidence_item': [r['id'] for r in result['evidence']],
            'evidence_file': [r['id'] for item in result['evidence'] for r in item['files']],
            'evidence_custody_event': [r['id'] for item in result['evidence'] for r in item['custody_history']]}
        for entity, section in [('complaint_decision','decisions'),('refusal_escalation','refusal_escalations'),
                ('docket_approval','approvals'),('case_assignment','assignments'),('docket_status_history','status_history'),
                ('investigation_note','investigation_notes'),('case_feedback','official_feedback'),
                ('official_document','documents'),('complaint_upload','complainant_uploads'),('notification','communications')]:
            entities[entity] = [r['id'] for r in result[section]]
        self.db.flush()  # Include the read that opened this dossier in its trail.
        logs = self.db.scalars(select(AuditLog).where(or_(*[
            and_(AuditLog.entity_type == kind, AuditLog.entity_id.in_(ids)) for kind, ids in entities.items() if ids]))
            .order_by(AuditLog.occurred_at, AuditLog.id))
        trail = []
        for entry in logs:
            user = self.db.get(User, entry.actor_user_id) if entry.actor_user_id else None
            officer = self.db.scalar(select(Officer).where(Officer.user_id == user.id)) if user else None
            actor = f'{officer.rank} {user.username} ({officer.service_number})' if officer else user.username if user else entry.actor_type.title()
            trail.append({'id': entry.id, 'occurred_at': entry.occurred_at, 'actor': actor,
                'action': entry.action, 'record_type': entry.entity_type})
        return trail
