from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import Permission, Role, User
from app.modules.authentication import schemas
from app.modules.authentication.dependencies import get_current_active_user, get_user_permissions, get_user_roles
from app.modules.authentication.service import AuthService

router = APIRouter(prefix='/auth', tags=['Authentication'])


def get_auth_service(request: Request, db: Session = Depends(get_db)) -> AuthService:
    # Do not trust arbitrary forwarded IP headers. Configure trusted proxies at deployment.
    return AuthService(db, request.client.host if request.client else None, request.headers.get('user-agent'))


@router.post('/register', response_model=schemas.LoginResponse, status_code=201)
def register(data: schemas.RegistrationRequest, service: AuthService = Depends(get_auth_service)):
    return service.register(data)


@router.post('/login', response_model=schemas.LoginResponse)
def login(data: schemas.LoginRequest, service: AuthService = Depends(get_auth_service)):
    return service.login(data)


@router.post('/mfa/setup', deprecated=True)
def setup(data: schemas.MfaSetupRequest, service: AuthService = Depends(get_auth_service)):
    return service.setup_mfa(data.challenge_token.get_secret_value())


@router.post('/mfa/verify-setup', deprecated=True)
def verify_setup(data: schemas.MfaSetupVerificationRequest, service: AuthService = Depends(get_auth_service)):
    return service.verify_setup(data.setup_token.get_secret_value(), data.code.get_secret_value())


@router.post('/mfa/verify', response_model=schemas.LoginResponse, deprecated=True)
@router.post('/email/transition', response_model=schemas.LoginResponse)
def verify_login(data: schemas.MfaLoginVerificationRequest, service: AuthService = Depends(get_auth_service)):
    return service.verify_login(data.challenge_token.get_secret_value(), data.code.get_secret_value())


@router.post('/refresh', response_model=schemas.TokenResponse)
def refresh(data: schemas.RefreshTokenRequest, service: AuthService = Depends(get_auth_service)):
    return service.refresh(data.refresh_token.get_secret_value())


@router.post('/logout', status_code=204)
def logout(data: schemas.LogoutRequest, service: AuthService = Depends(get_auth_service)):
    service.logout(data.refresh_token.get_secret_value())
    return Response(status_code=204)


@router.get('/me', response_model=schemas.CurrentUserResponse)
def current_user(user: User = Depends(get_current_active_user), roles: list[Role] = Depends(get_user_roles),
                 permissions: list[Permission] = Depends(get_user_permissions)):
    return schemas.CurrentUserResponse(id=user.id, username=user.username, email=user.email,
        is_active=user.is_active, is_verified=user.is_verified, mfa_enabled=user.mfa_enabled,
        roles=[schemas.RoleSummary.model_validate(role) for role in roles],
        permissions=[schemas.PermissionSummary.model_validate(permission) for permission in permissions],
        complainant_id=user.complainant.id if user.complainant else None,
        officer_id=user.officer.id if user.officer else None)


@router.post('/email/verify', response_model=schemas.TokenResponse)
def verify_email(data: schemas.MfaLoginVerificationRequest, service: AuthService = Depends(get_auth_service)):
    return service.verify_email(data.challenge_token.get_secret_value(), data.code.get_secret_value())


@router.post('/email/resend', response_model=schemas.LoginResponse)
def resend_email(data: schemas.MfaSetupRequest, service: AuthService = Depends(get_auth_service)):
    return service.resend_email(data.challenge_token.get_secret_value())
