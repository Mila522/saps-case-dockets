import re
import uuid
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RegistrationRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=12, max_length=1024)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone_number: str = Field(min_length=1, max_length=30)
    preferred_contact_method: Literal['SMS', 'EMAIL', 'PHONE'] = 'EMAIL'

    @field_validator('username', 'email', mode='before')
    @classmethod
    def normalize(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator('password')
    @classmethod
    def password_strength(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if password != password.strip() or not (any(c.isupper() for c in password) and
            any(c.islower() for c in password) and any(c.isdigit() for c in password) and
            re.search(r'[^\w\s]', password)):
            raise ValueError('Password requires uppercase, lowercase, a number, a special character, and no surrounding whitespace')
        return value


class LoginRequest(StrictRequest):
    username: str = Field(min_length=1, max_length=254, validation_alias=AliasChoices('username', 'identifier'))
    password: SecretStr = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    status: Literal['MFA_SETUP_REQUIRED', 'MFA_REQUIRED']
    challenge_token: str
    expires_in: int


class MfaSetupRequest(StrictRequest):
    challenge_token: SecretStr = Field(min_length=1, max_length=4096)


class MfaSetupResponse(BaseModel):
    provisioning_uri: str
    manual_entry_secret: str
    setup_token: str
    expires_in: int


class MfaSetupVerificationRequest(StrictRequest):
    setup_token: SecretStr = Field(min_length=1, max_length=4096)
    code: SecretStr = Field(min_length=6, max_length=6)


class MfaLoginVerificationRequest(MfaSetupRequest):
    code: SecretStr = Field(min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal['bearer'] = 'bearer'
    expires_in: int


class RefreshTokenRequest(StrictRequest):
    refresh_token: SecretStr = Field(min_length=1, max_length=128)


class LogoutRequest(RefreshTokenRequest):
    pass


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str


class PermissionSummary(RoleSummary):
    pass


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    is_verified: bool
    mfa_enabled: bool
    roles: list[RoleSummary]
    permissions: list[PermissionSummary]
    complainant_id: uuid.UUID | None
    officer_id: uuid.UUID | None
