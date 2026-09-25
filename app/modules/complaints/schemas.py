"""Public tracking data deliberately excludes statements and investigation details."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, EmailStr
from app.modules.evidence.schemas import EvidenceOut


class ReceivingStationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    province: str


class ComplaintTracking(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference_number: str
    status: str
    station_id: uuid.UUID
    submitted_at: datetime
    review_started_at: datetime | None
    updated_at: datetime
    cas_number: str | None = None


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
    registered_by_officer_id: uuid.UUID | None = None
    docket_id: uuid.UUID | None = None
    cas_number: str | None = None


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


class StatementRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    statement_text: str = Field(min_length=1, max_length=20000)
    expected_version: int = Field(default=0, ge=0)


class WitnessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone_number: str | None = Field(default=None, min_length=1, max_length=30)
    email: EmailStr | None = Field(default=None, max_length=254)
    address: str | None = Field(default=None, min_length=1, max_length=4000)
    statement_text: str | None = Field(default=None, min_length=1, max_length=20000)


class WalkInComplainant(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone_number: str = Field(min_length=1, max_length=30)
    email: EmailStr | None = Field(default=None, max_length=254)
    preferred_contact_method: Literal['PHONE', 'SMS', 'EMAIL'] = 'PHONE'


class InStationRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    complainant: WalkInComplainant
    details_confirmed_with_complainant: Literal[True]
    crime_category: str = Field(min_length=1, max_length=150)
    incident_description: str = Field(min_length=1, max_length=20000)
    incident_occurred_at: AwareDatetime | None = None
    incident_location: str = Field(min_length=1, max_length=255)
    incident_city: str | None = Field(default=None, min_length=1, max_length=150)
    incident_province: str = Field(min_length=1, max_length=100)
    witnesses: list[WitnessRequest] = Field(default_factory=list, max_length=50)


class ReferenceTrackingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reference_number: str = Field(min_length=1, max_length=100)


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    statement_text: str
    statement_version: int
    is_current: bool
    recorded_by_officer_id: uuid.UUID | None
    signed_at: datetime | None
    created_at: datetime


class WitnessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    first_name: str
    last_name: str
    phone_number: str | None
    email: str | None
    address: str | None
    statements: list[StatementOut]


class ComplaintMaterialOut(BaseModel):
    complaint_id: uuid.UUID
    statements: list[StatementOut]
    witnesses: list[WitnessOut]
    can_append: bool
    initial_evidence: list[EvidenceOut] = Field(default_factory=list)
