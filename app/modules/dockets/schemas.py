import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    complaint_id: uuid.UUID
    cas_number: str
    status: str
    created_from_decision_id: uuid.UUID
    opened_by_officer_id: uuid.UUID
    opened_at: datetime
    closed_at: datetime | None
    closure_reason: str | None


class DocketApprovalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    decision: Literal['APPROVED', 'RETURNED_FOR_CORRECTION']
    notes: str | None = Field(default=None, max_length=20000)

    @model_validator(mode='after')
    def correction_requires_notes(self) -> 'DocketApprovalRequest':
        if self.decision == 'RETURNED_FOR_CORRECTION' and not (self.notes and self.notes.strip()):
            raise ValueError('notes are required when returning a docket for correction')
        return self


class DocketApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    docket_id: uuid.UUID
    decision: str
    notes: str | None
    decision_sequence: int
    decided_at: datetime
    docket_status: str
