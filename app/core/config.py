from pathlib import Path
from typing import Literal
import base64

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = Field(repr=False)
    migration_database_url: str = Field(repr=False)
    environment: Literal['development', 'test', 'production'] = 'development'
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal['HS256'] = 'HS256'
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)
    mfa_encryption_key: SecretStr
    max_failed_login_attempts: int = Field(default=5, gt=0)
    account_lock_minutes: int = Field(default=15, gt=0)
    mfa_challenge_expire_minutes: int = Field(default=5, gt=0, le=10)
    evidence_storage_path: Path = Path(__file__).resolve().parents[2] / '.evidence-storage'
    document_storage_path: Path = Path(__file__).resolve().parents[2] / '.document-storage'
    alert_inactivity_days: int = Field(default=7, ge=1, le=365)
    alert_docket_approval_hours: int = Field(default=48, ge=1, le=8760)
    alert_escalation_hours: int = Field(default=24, ge=1, le=8760)
    evidence_max_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=100 * 1024 * 1024)

    @field_validator('jwt_secret_key')
    @classmethod
    def valid_signing_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret.encode()) < 32 or len(set(secret)) < 8 or any(
            marker in secret.lower() for marker in ('placeholder', 'change-me', 'changeme', 'your-secret', 'replace-me')
        ):
            raise ValueError('A strong, non-placeholder JWT secret of at least 32 bytes is required')
        return value

    @field_validator('mfa_encryption_key')
    @classmethod
    def valid_encryption_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode())
            if len(set(base64.urlsafe_b64decode(value.get_secret_value()))) < 8:
                raise ValueError('Placeholder encryption key')
        except (ValueError, TypeError):
            raise ValueError('A valid Fernet encryption key is required') from None
        return value

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

settings = Settings()
