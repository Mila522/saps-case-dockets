"""Decision requests are officer-facing; refusal reasons are a read-only controlled list."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RefusalReasonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    requires_escalation: bool
    requires_officer_notes: bool


class ComplaintDecisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    decision: Literal['ACCEPTED', 'REFUSED']
    refusal_reason_id: uuid.UUID | None = None
    officer_notes: str | None = Field(default=None, max_length=20000)

    @model_validator(mode='after')
    def check_reason_matches_decision(self) -> 'ComplaintDecisionRequest':
        # Mirrors the DB check constraint so bad input gets a clean 422, not a raw DB error.
        if self.decision == 'ACCEPTED' and self.refusal_reason_id is not None:
            raise ValueError('refusal_reason_id must not be set when decision is ACCEPTED')
        if self.decision == 'REFUSED' and self.refusal_reason_id is None:
            raise ValueError('refusal_reason_id is required when decision is REFUSED')
        return self


class ComplaintDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    complaint_id: uuid.UUID
    decision: str
    refusal_reason_id: uuid.UUID | None
    officer_notes: str | None
    decision_sequence: int
    decided_at: datetime
    complaint_status: str


class RefusalEscalationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    complaint_id: uuid.UUID
    complaint_decision_id: uuid.UUID
    target: str
    status: str
    escalated_at: datetime
    acknowledged_by_user_id: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolution_notes: str | None


class EscalationResolutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    resolution_notes: str = Field(min_length=1, max_length=20000)
