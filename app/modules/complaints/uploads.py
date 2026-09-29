"""Private, ownership-checked complaint files, including pre-docket evidence."""
from datetime import timedelta
from pathlib import PurePosixPath
from uuid import UUID
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import get_current_active_user
from app.modules.authentication.repository import AuthRepository
from app.modules.authentication.security import utcnow
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint, ComplaintUpload
from app.modules.complaints.intake import ComplaintIntakeService
from app.modules.evidence.storage import EvidenceStorage
from app.modules.audit.models import AuditLog

router = APIRouter(prefix='/complaints', tags=['Complaint uploads'])
ALLOWED = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.webm', '.mp3', '.wav', '.m4a', '.ogg', '.pdf', '.doc', '.docx', '.txt', '.odt', '.csv'}
DOCUMENTS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf', '.doc', '.docx', '.odt'}


def public(row):
    return {key: getattr(row, key) for key in ('id', 'purpose', 'original_filename', 'file_size_bytes', 'uploaded_at')}


def scope(db, user, complaint_id):
    complaint = db.get(Complaint, complaint_id)
    person = db.scalar(select(Complainant).where(Complainant.user_id == user.id))
    if complaint and person and complaint.complainant_id == person.id:
        return complaint
    return ComplaintIntakeService(db).scope(user.id, complaint_id)[0]


def attach(db, actor, complaint, upload_ids):
    rows = list(db.scalars(select(ComplaintUpload).where(ComplaintUpload.id.in_(upload_ids))
        .order_by(ComplaintUpload.id).with_for_update().execution_options(populate_existing=True))) if upload_ids else []
    if len(rows) != len(upload_ids) or any(r.uploaded_by_user_id != actor or r.complaint_id is not None
            or r.uploaded_at < utcnow() - timedelta(days=1) for r in rows):
        raise HTTPException(422, 'Select your own unused uploads from the last 24 hours')
    if complaint.crime_category == 'Vehicle theft' and not any(r.purpose == 'VEHICLE_REGISTRATION' for r in rows):
        raise HTTPException(422, 'Vehicle theft requires a vehicle registration document upload')
    for row in rows:
        row.complaint_id = complaint.id


@router.post('/uploads', status_code=201)
def upload(file: UploadFile = File(...), purpose: Literal['EVIDENCE', 'VEHICLE_REGISTRATION'] = Form('EVIDENCE'),
        complaint_id: UUID | None = Form(None), user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    permissions = {p.code for p in AuthRepository(db).permissions(user.id)}
    if not permissions.intersection({'complaint.submit', 'complaint.register'}):
        raise HTTPException(403, 'Complaint intake permission required')
    # Serialize per-account quotas, and check ownership before saving bytes.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    if complaint_id:
        complaint = scope(db, user, complaint_id)
        if complaint.status in {'REFUSED', 'ESCALATED'}:
            raise HTTPException(409, 'Uploads are closed for this complaint')
    count = db.scalar(select(func.count()).select_from(ComplaintUpload).where(
        ComplaintUpload.uploaded_by_user_id == user.id, ComplaintUpload.uploaded_at > utcnow() - timedelta(days=1)))
    if count >= 50:
        raise HTTPException(429, 'Daily upload limit reached')
    name = PurePosixPath((file.filename or '').replace('\\', '/')).name
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise HTTPException(422, 'Invalid filename')
    extension = PurePosixPath(name).suffix.lower()
    if extension not in (DOCUMENTS if purpose == 'VEHICLE_REGISTRATION' else ALLOWED):
        raise HTTPException(422, 'Choose an image, video, audio recording or supported document')
    storage = EvidenceStorage()
    key, size, digest = storage.save(file.file)
    committing = False
    try:
        row = ComplaintUpload(uploaded_by_user_id=user.id, complaint_id=complaint_id, purpose=purpose,
            original_filename=name, media_type='application/octet-stream', storage_key=key,
            file_size_bytes=size, sha256_hash=digest)
        db.add(row)
        db.flush()
        db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action='complaint.upload', entity_type='complaint_upload', entity_id=row.id))
        result = public(row)
        committing = True
        db.commit()
        return result
    except Exception:
        db.rollback()
        if not committing:
            storage.discard(key)
        raise


@router.get('/{complaint_id}/uploads')
def list_uploads(complaint_id: UUID, user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    scope(db, user, complaint_id)
    rows = list(db.scalars(select(ComplaintUpload).where(ComplaintUpload.complaint_id == complaint_id).order_by(ComplaintUpload.uploaded_at)))
    result = [public(r) for r in rows]
    db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action='complaint.uploads.view', entity_type='complaint', entity_id=complaint_id))
    db.commit()
    return result


@router.get('/{complaint_id}/uploads/{upload_id}/download')
def download(complaint_id: UUID, upload_id: UUID, user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    scope(db, user, complaint_id)
    row = db.scalar(select(ComplaintUpload).where(ComplaintUpload.id == upload_id, ComplaintUpload.complaint_id == complaint_id))
    if row is None:
        raise HTTPException(404, 'File not found')
    content = EvidenceStorage().read_verified(row)
    filename = quote(row.original_filename, safe='')
    db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action='complaint.upload.download', entity_type='complaint_upload', entity_id=row.id))
    db.commit()
    return Response(content, media_type='application/octet-stream', headers={'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
        'Content-Disposition': "attachment; filename*=UTF-8''" + filename})
