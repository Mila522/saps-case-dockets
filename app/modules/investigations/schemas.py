import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.modules.dockets.schemas import DocketOut


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ErrorResponse(BaseModel):
    detail: str | list[dict]


class InvestigatorDocketOut(DocketOut):
    allowed_next_statuses: list[str] = Field(default_factory=list)
    complaint_reference: str
    crime_category: str
    station_id: uuid.UUID
    station_name: str
    assigned_at: datetime
    investigating_officer_id: uuid.UUID
    updated_at: datetime
    incident_description: str | None = None
    incident_occurred_at: datetime | None = None
    incident_location: str | None = None


class StatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_status: str | None
    to_status: str
    changed_by_user_id: uuid.UUID
    change_reason: str | None
    changed_at: datetime


ERROR_RESPONSES = {code: {'model': ErrorResponse, 'description': description} for code, description in {
    401: 'Missing, expired or revoked authentication',
    403: 'Required permission, active officer profile or role missing',
    404: 'Resource missing or outside the actor station/assignment scope',
    409: 'Stale state, invalid transition, duplicate operation or file integrity conflict',
    422: 'Request validation failed',
    503: 'Database or protected storage unavailable',
}.items()}


class AssignmentRequest(RequestModel):
    investigating_officer_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=4000)


class ReasonRequest(RequestModel):
    reason: str = Field(min_length=1, max_length=4000,
        description='Required explanation. For CLOSED this is persisted as the docket closure_reason.')


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    docket_id: uuid.UUID
    investigating_officer_id: uuid.UUID
    assigned_by_officer_id: uuid.UUID
    assigned_at: datetime
    unassigned_at: datetime | None
    assignment_reason: str | None
    unassignment_reason: str | None


class NoteRequest(RequestModel):
    note_type: Literal['GENERAL', 'PROGRESS', 'LEAD', 'INTERVIEW', 'FORENSIC', 'ADMINISTRATIVE', 'CORRECTION'] = 'GENERAL'
    content: str = Field(min_length=1, max_length=20000, examples=['Interview completed; follow-up investigation recorded.'])
    is_sensitive: bool = False


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    docket_id: uuid.UUID
    author_officer_id: uuid.UUID
    note_type: str
    content: str
    is_sensitive: bool
    created_at: datetime


class StatusRequest(ReasonRequest):
    model_config = ConfigDict(json_schema_extra={'examples': [
        {'expected_status': 'ACTIVE', 'status': 'ON_HOLD', 'reason': 'Awaiting laboratory analysis'},
        {'expected_status': 'ON_HOLD', 'status': 'CLOSED', 'reason': 'Investigation concluded'},
    ]})
    status: Literal['ACTIVE', 'ON_HOLD', 'CLOSED']
    expected_status: Literal['ACTIVE', 'ON_HOLD'] = Field(
        description='Current status observed by the caller. A stale value returns 409.', examples=['ACTIVE'])
