import uuid
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from app.modules.authentication.schemas import RegistrationRequest

STAFF_ROLES = ('CHARGE_OFFICER', 'STATION_COMMANDER', 'INVESTIGATING_OFFICER')


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=12, max_length=1024, repr=False)

    @field_validator('username', 'email', mode='before')
    @classmethod
    def normalize(cls, value):
        return RegistrationRequest.normalize(value)

    @field_validator('password')
    @classmethod
    def password_strength(cls, value):
        return RegistrationRequest.password_strength(value)


class StaffDetails(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    role: Literal['CHARGE_OFFICER', 'STATION_COMMANDER', 'INVESTIGATING_OFFICER']
    station_id: uuid.UUID
    service_number: str = Field(min_length=1, max_length=50)
    rank: str = Field(min_length=1, max_length=100)
    phone_number: str | None = Field(default=None, max_length=30)


class StaffCreate(Credentials, StaffDetails):
    pass


class StaffUpdate(StaffDetails):
    expected_updated_at: AwareDatetime
    is_active: bool
