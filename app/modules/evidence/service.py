import re

from fastapi import HTTPException
from sqlalchemy import func, select

from app.modules.authentication.security import utcnow
from app.modules.evidence.models import EvidenceItem, EvidenceFile, EvidenceCustodyEvent
from app.modules.evidence.schemas import EvidenceOut, FileOut, CustodyOut
from app.modules.evidence.storage import EvidenceStorage
from app.modules.investigations.service import InvestigationService, transactional
from app.modules.stations.models import Officer, Station
from app.modules.system.service import allocate_evidence_reference


class EvidenceService(InvestigationService):
    def item_scope(self, user_id, evidence_id, *, writable=False):
        docket_id = self.db.scalar(select(EvidenceItem.docket_id).where(EvidenceItem.id == evidence_id))
        if docket_id is None:
            raise HTTPException(404, 'Evidence not found')
        officer, docket = self.scope(user_id, docket_id, writable=writable)
        item = self.db.scalar(select(EvidenceItem).where(EvidenceItem.id == evidence_id).with_for_update())
        if writable and item.status in ('RELEASED', 'DISPOSED'):
            raise HTTPException(409, 'Evidence is no longer in active custody')
        return officer, docket, item

    @transactional
    def register(self, user_id, docket_id, data):
        officer, docket = self.scope(user_id, docket_id, writable=True)
        station = self.db.get(Station, officer.station_id)
        row = EvidenceItem(docket_id=docket.id,
            evidence_reference=allocate_evidence_reference(self.db, station, utcnow()),
            registered_by_user_id=user_id, registered_at=utcnow(), status='IN_CUSTODY',
            current_custodian_officer_id=officer.id, current_storage_location=data.storage_location,
            collected_by_officer_id=officer.id if data.collected_at else None,
            **data.model_dump(exclude={'storage_location'}))
        self.db.add(row)
        self.db.flush()
        self.db.add(EvidenceCustodyEvent(evidence_item_id=row.id, event_type='REGISTERED',
            performed_by_user_id=user_id, to_custodian_officer_id=officer.id,
            to_location=data.storage_location, occurred_at=utcnow()))
        self.audit(user_id, officer.station_id, 'evidence.register', 'evidence_item', row.id)
        return EvidenceOut.model_validate(row)

    @transactional
    def list_items(self, user_id, docket_id, limit, offset):
        officer, docket = self.scope(user_id, docket_id)
        rows = self.db.scalars(select(EvidenceItem).where(EvidenceItem.docket_id == docket.id)
            .order_by(EvidenceItem.registered_at, EvidenceItem.id).limit(limit).offset(offset))
        result = [EvidenceOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'evidence.list', 'docket', docket.id)
        return result

    @transactional
    def get_item(self, user_id, evidence_id):
        officer, _, item = self.item_scope(user_id, evidence_id)
        self.audit(user_id, officer.station_id, 'evidence.view', 'evidence_item', item.id)
        return EvidenceOut.model_validate(item)

    @transactional
    def custody(self, user_id, evidence_id, data):
        officer, _, item = self.item_scope(user_id, evidence_id, writable=True)
        transitions = {
            'TRANSFERRED': ({'IN_CUSTODY', 'TRANSFERRED'}, 'TRANSFERRED'),
            'ANALYSIS_STARTED': ({'IN_CUSTODY', 'TRANSFERRED'}, 'UNDER_ANALYSIS'),
            'ANALYSIS_COMPLETED': ({'UNDER_ANALYSIS'}, 'IN_CUSTODY'),
            'RELEASED': ({'IN_CUSTODY', 'TRANSFERRED'}, 'RELEASED'),
            'DISPOSED': ({'IN_CUSTODY', 'TRANSFERRED'}, 'DISPOSED'),
        }
        allowed, status = transitions[data.event_type]
        if item.status not in allowed:
            raise HTTPException(409, 'Invalid custody transition')
        destination = item.current_custodian_officer_id
        if data.event_type == 'TRANSFERRED':
            target = self.db.scalar(select(Officer).where(Officer.id == data.to_custodian_officer_id))
            if target is None or target.station_id != officer.station_id:
                raise HTTPException(422, 'Select an active custodian at this station')
            self.officer(target.user_id, 'INVESTIGATING_OFFICER')
            if target.id == item.current_custodian_officer_id:
                raise HTTPException(409, 'Evidence is already held by that custodian')
            destination = target.id
        elif data.event_type in ('RELEASED', 'DISPOSED'):
            destination = None
        event = EvidenceCustodyEvent(evidence_item_id=item.id, event_type=data.event_type,
            performed_by_user_id=user_id, from_custodian_officer_id=item.current_custodian_officer_id,
            to_custodian_officer_id=destination, from_location=item.current_storage_location,
            to_location=data.to_location, event_notes=data.notes, occurred_at=utcnow())
        self.db.add(event)
        item.current_custodian_officer_id = destination
        item.current_storage_location = data.to_location
        item.status = status
        self.db.flush()
        self.audit(user_id, officer.station_id, 'evidence.custody.' + data.event_type.lower(), 'evidence_item', item.id)
        return CustodyOut.model_validate(event)

    @transactional
    def custody_history(self, user_id, evidence_id, limit, offset):
        officer, _, item = self.item_scope(user_id, evidence_id)
        rows = self.db.scalars(select(EvidenceCustodyEvent).where(EvidenceCustodyEvent.evidence_item_id == item.id)
            .order_by(EvidenceCustodyEvent.occurred_at, EvidenceCustodyEvent.id).limit(limit).offset(offset))
        result = [CustodyOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'evidence.custody.view', 'evidence_item', item.id)
        return result

    def upload(self, user_id, evidence_id, file):
        # Clean up pre-commit failures. A lost connection during COMMIT has an
        # uncertain outcome: preserve the private object for reconciliation rather
        # than risk deleting evidence whose metadata was actually committed.
        storage, pending = EvidenceStorage(), []
        try:
            return self._upload(user_id, evidence_id, file, storage, pending)
        except Exception:
            for key in ([] if self._commit_started else pending):
                try:
                    storage.discard(key)
                except OSError:
                    pass  # A private orphan can be reconciled; never mask the API failure.
            raise

    @transactional
    def _upload(self, user_id, evidence_id, file, storage, pending):
        officer, _, item = self.item_scope(user_id, evidence_id, writable=True)
        filename = (file.filename or '').replace('\\', '/').split('/')[-1]
        filename = re.sub(r'[\x00-\x1f\x7f]', '', filename).strip()
        if not filename or filename in ('.', '..') or len(filename) > 255:
            raise HTTPException(422, 'A filename of at most 255 characters is required')
        media_type = file.content_type or 'application/octet-stream'
        if len(media_type) > 255 or any(ord(char) < 32 for char in media_type):
            raise HTTPException(422, 'Invalid media type')
        key, size, digest = storage.save(file.file)
        pending.append(key)
        version = (self.db.scalar(select(func.max(EvidenceFile.file_version)).where(
            EvidenceFile.evidence_item_id == item.id)) or 0) + 1
        row = EvidenceFile(evidence_item_id=item.id, file_version=version, original_filename=filename,
            storage_key=key, media_type=media_type, file_size_bytes=size, sha256_hash=digest,
            uploaded_by_user_id=user_id, uploaded_at=utcnow())
        self.db.add(row)
        self.db.flush()
        self.audit(user_id, officer.station_id, 'evidence.file.upload', 'evidence_file', row.id)
        return FileOut.model_validate(row)

    @transactional
    def files(self, user_id, evidence_id, limit, offset):
        officer, _, item = self.item_scope(user_id, evidence_id)
        rows = self.db.scalars(select(EvidenceFile).where(EvidenceFile.evidence_item_id == item.id)
            .order_by(EvidenceFile.file_version).limit(limit).offset(offset))
        result = [FileOut.model_validate(row) for row in rows]
        self.audit(user_id, officer.station_id, 'evidence.files.view', 'evidence_item', item.id)
        return result

    @transactional
    def download(self, user_id, evidence_id, file_id):
        officer, _, item = self.item_scope(user_id, evidence_id)
        row = self.db.scalar(select(EvidenceFile).where(
            EvidenceFile.id == file_id, EvidenceFile.evidence_item_id == item.id))
        if row is None:
            raise HTTPException(404, 'Evidence file not found')
        data = EvidenceStorage().read_verified(row)
        self.db.add(EvidenceCustodyEvent(evidence_item_id=item.id, event_type='ACCESSED',
            performed_by_user_id=user_id, from_custodian_officer_id=item.current_custodian_officer_id,
            to_custodian_officer_id=item.current_custodian_officer_id,
            from_location=item.current_storage_location, to_location=item.current_storage_location,
            occurred_at=utcnow()))
        self.audit(user_id, officer.station_id, 'evidence.file.download', 'evidence_file', row.id)
        return data, row.original_filename
