from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import CurrentPrincipalDep
from app.core.config import get_settings
from app.core.database import DbSession
from app.core.exceptions import AppError
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    OrganizationResponse,
    RegisterRequest,
    UserResponse,
)
from app.services.auth import AuthResult, login_user, refresh_session, register_user

router = APIRouter(prefix="/auth")


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=f"{settings.api_v1_prefix}/auth",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _auth_response(result: AuthResult) -> AuthResponse:
    return AuthResponse(
        access_token=result.access_token,
        user=UserResponse(
            public_id=result.user.public_id,
            email=result.user.email,
            is_active=result.user.is_active,
        ),
        organization=OrganizationResponse(
            public_id=result.organization.public_id,
            name=result.organization.name,
            status=result.organization.status.value,
            role=result.membership.role.value,
        ),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    response: Response,
    session: DbSession,
) -> AuthResponse:
    result = await register_user(session, payload=payload)
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
) -> AuthResponse:
    result = await login_user(session, payload=payload)
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
) -> AuthResponse:
    settings = get_settings()
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token is None:
        raise AppError("Invalid or expired refresh token", code=401)
    result = await refresh_session(session, refresh_token=refresh_token)
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipalDep) -> MeResponse:
    return MeResponse(
        user=UserResponse(
            public_id=principal.user_public_id,
            email=principal.email,
            is_active=True,
        ),
        organization=OrganizationResponse(
            public_id=principal.organization_public_id,
            name=principal.organization_name,
            status=principal.organization_status.value,
            role=principal.role.value,
        ),
    )
