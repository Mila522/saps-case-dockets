import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class AssignmentRequest(RequestModel):
    investigating_officer_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=4000)


class ReasonRequest(RequestModel):
    reason: str = Field(min_length=1, max_length=4000)


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
    content: str = Field(min_length=1, max_length=20000)
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
    status: Literal['ACTIVE', 'ON_HOLD', 'CLOSED']
