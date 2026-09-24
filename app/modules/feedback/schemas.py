from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FeedbackType = Literal['REGISTRATION', 'PROGRESS_UPDATE', 'REQUEST_FOR_INFORMATION', 'REFUSAL', 'CASE_CLOSURE', 'GENERAL']


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    feedback_type: FeedbackType
    subject: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=10000)
    supersedes_feedback_id: UUID | None = None


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    docket_id: UUID
    complainant_id: UUID
    provided_by_officer_id: UUID
    feedback_type: FeedbackType
    subject: str
    message: str
    is_official: bool
    supersedes_feedback_id: UUID | None
    published_at: datetime
    created_at: datetime


class FeedbackPage(BaseModel):
    items: list[FeedbackResponse]
    limit: int
    offset: int
