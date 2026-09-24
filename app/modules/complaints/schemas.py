"""Public tracking data deliberately excludes statements and investigation details."""
import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ComplaintTracking(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference_number: str
    status: str
    station_id: uuid.UUID
    submitted_at: datetime
    review_started_at: datetime | None
    updated_at: datetime


class ComplaintTrackingPage(BaseModel):
    items: list[ComplaintTracking]
    limit: int
    offset: int
    has_more: bool


class StationComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference_number: str
    status: str
    station_id: uuid.UUID
    channel: str
    crime_category: str
    incident_description: str
    incident_occurred_at: datetime | None
    incident_location: str
    incident_city: str | None
    incident_province: str
    submitted_at: datetime
    review_started_at: datetime | None
    updated_at: datetime


class StationComplaintPage(BaseModel):
    items: list[StationComplaintOut]
    limit: int
    offset: int
    has_more: bool


class ComplaintRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    station_id: uuid.UUID
    crime_category: str = Field(min_length=1, max_length=150)
    incident_description: str = Field(min_length=1, max_length=20000)
    incident_occurred_at: AwareDatetime | None = None
    incident_location: str = Field(min_length=1, max_length=255)
    incident_city: str | None = Field(default=None, min_length=1, max_length=150)
    incident_province: str = Field(min_length=1, max_length=100)
