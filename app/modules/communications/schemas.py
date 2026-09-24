from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    complaint_id: UUID | None
    docket_id: UUID | None
    channel: str
    event_type: str
    subject: str | None
    message: str
    status: str
    created_at: datetime


DocumentType = Literal['COMPLAINT_REGISTRATION_CONFIRMATION', 'REFUSAL_CONFIRMATION',
    'DOCKET_REGISTRATION_CONFIRMATION', 'CASE_CLOSURE_CONFIRMATION']


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_type: DocumentType


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    complaint_id: UUID | None
    docket_id: UUID | None
    document_type: str
    document_number: str
    media_type: str
    file_size_bytes: int
    sha256_hash: str
    generated_at: datetime


class AlertTransition(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_status: Literal['OPEN', 'ACKNOWLEDGED']
    status: Literal['ACKNOWLEDGED', 'RESOLVED', 'DISMISSED']
    notes: str = Field(min_length=1, max_length=2000)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    station_id: UUID
    complaint_id: UUID | None
    docket_id: UUID | None
    alert_type: str
    severity: str
    status: str
    title: str
    description: str
    assigned_to_user_id: UUID | None
    detected_at: datetime
    due_at: datetime | None
    acknowledged_by_user_id: UUID | None
    acknowledged_at: datetime | None
    resolved_by_user_id: UUID | None
    resolved_at: datetime | None
    resolution_notes: str | None
