import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.modules.authentication.security import utcnow
from app.modules.investigations.schemas import RequestModel


class EvidenceRequest(RequestModel):
    model_config = ConfigDict(json_schema_extra={'examples': [{
        'title': 'Camera recording', 'description': 'Recording supplied for investigation',
        'evidence_type': 'VIDEO', 'is_digital': True, 'storage_location': 'Secure locker A',
    }]})
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=20000)
    evidence_type: Literal['DOCUMENT', 'IMAGE', 'VIDEO', 'AUDIO', 'PHYSICAL_OBJECT', 'DIGITAL_DEVICE', 'OTHER']
    is_digital: bool
    collected_at: AwareDatetime | None = None
    collection_location: str | None = Field(default=None, min_length=1, max_length=4000)
    storage_location: str = Field(min_length=1, max_length=255)

    @model_validator(mode='after')
    def not_future(self):
        if self.collected_at and self.collected_at > utcnow():
            raise ValueError('Collection time cannot be in the future')
        return self


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    docket_id: uuid.UUID
    evidence_reference: str
    title: str
    description: str
    evidence_type: str
    is_digital: bool
    collected_at: datetime | None
    collected_by_officer_id: uuid.UUID | None
    collection_location: str | None
    registered_by_user_id: uuid.UUID
    registered_at: datetime
    status: str
    current_custodian_officer_id: uuid.UUID | None
    current_storage_location: str | None
    custody_version: uuid.UUID | None = Field(default=None,
        description='Latest custody-changing event ID; submit as expected_custody_event_id when changing custody.')


class CustodyRequest(RequestModel):
    model_config = ConfigDict(json_schema_extra={'examples': [{
        'event_type': 'TRANSFERRED', 'expected_custody_event_id': '00000000-0000-4000-8000-000000000001',
        'to_custodian_officer_id': '00000000-0000-4000-8000-000000000002',
        'to_location': 'Laboratory locker B', 'notes': 'Signed handover recorded',
    }]})
    expected_custody_event_id: uuid.UUID = Field(
        description='custody_version from the latest evidence response. Stale values return 409.')
    event_type: Literal['TRANSFERRED', 'ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'RELEASED', 'DISPOSED']
    to_custodian_officer_id: uuid.UUID | None = None
    to_location: str = Field(min_length=1, max_length=255)
    notes: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def destination(self):
        if (self.event_type == 'TRANSFERRED') != (self.to_custodian_officer_id is not None):
            raise ValueError('A destination officer is required only for transfers')
        return self


class CustodyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    evidence_item_id: uuid.UUID
    event_type: str
    performed_by_user_id: uuid.UUID
    from_custodian_officer_id: uuid.UUID | None
    to_custodian_officer_id: uuid.UUID | None
    from_location: str | None
    to_location: str | None
    event_notes: str | None
    occurred_at: datetime


class FileOut(BaseModel):
    # Storage keys and filesystem paths never leave the storage service.
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    evidence_item_id: uuid.UUID
    file_version: int
    original_filename: str
    media_type: str
    file_size_bytes: int
    sha256_hash: str
    uploaded_by_user_id: uuid.UUID
    uploaded_at: datetime
