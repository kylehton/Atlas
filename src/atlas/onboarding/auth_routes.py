from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas.onboarding.service import (
    SettingsAccessDenied,
    SettingsIdentityMismatch,
    SettingsLoginUnavailable,
    SettingsProfile,
    SettingsService,
)
from atlas.shared.field_types import ShortText

PORTAL_SESSION_COOKIE = "atlas_portal_session"

router = APIRouter(prefix="/auth", tags=["portal authentication"])


class PortalLoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=32, max_length=128)


class TelegramAuthorizationPayload(BaseModel):
    authorization_url: str


class PortalProfilePayload(BaseModel):
    display_name: ShortText | None
    telegram_username: ShortText | None


@router.post("/telegram/start")
def start_telegram_login(
    payload: PortalLoginPayload,
    request: Request,
    response: Response,
) -> TelegramAuthorizationPayload:
    """Start OIDC only after resolving a short-lived link to its expected user."""

    service: SettingsService = request.app.state.settings_service
    try:
        login = service.begin_telegram_login(payload.request_token)
    except SettingsAccessDenied:
        raise _access_denied() from None
    except SettingsLoginUnavailable:
        raise _login_unavailable() from None
    response.headers["Cache-Control"] = "no-store"
    return TelegramAuthorizationPayload(authorization_url=login.authorization_url)


@router.get("/telegram/callback", response_model=None)
async def complete_telegram_login(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=4_096)],
    state_value: Annotated[
        str,
        Query(alias="state", min_length=32, max_length=128),
    ],
) -> RedirectResponse:
    """Create a shared portal session only when Telegram matches the expected user."""

    service: SettingsService = request.app.state.settings_service
    try:
        portal_session = await service.complete_telegram_login(
            code=code,
            state=state_value,
        )
    except SettingsIdentityMismatch:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram account does not match this portal request",
        ) from None
    except SettingsAccessDenied:
        raise _access_denied() from None
    except SettingsLoginUnavailable:
        raise _login_unavailable() from None

    response = RedirectResponse("/notifications", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        PORTAL_SESSION_COOKIE,
        portal_session.value,
        expires=portal_session.expires_at,
        httponly=True,
        secure=request.app.state.settings.environment != "development",
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/profile")
def get_portal_profile(request: Request, response: Response) -> PortalProfilePayload:
    """Expose a minimal profile only to an authenticated portal session."""

    service: SettingsService = request.app.state.settings_service
    try:
        profile = service.get_profile(portal_session_token(request))
    except SettingsAccessDenied:
        raise _access_denied() from None
    response.headers["Cache-Control"] = "no-store"
    return _profile_payload(profile)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_portal_session(request: Request) -> Response:
    """Revoke the server-side portal session and remove its browser cookie."""

    service: SettingsService = request.app.state.settings_service
    session_token = request.cookies.get(PORTAL_SESSION_COOKIE)
    if session_token is not None:
        service.revoke_session(session_token)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(PORTAL_SESSION_COOKIE, path="/", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return response


def portal_session_token(request: Request) -> str:
    session_token = request.cookies.get(PORTAL_SESSION_COOKIE)
    if session_token is None:
        raise SettingsAccessDenied
    return session_token


def access_denied() -> HTTPException:
    return _access_denied()


def _profile_payload(profile: SettingsProfile) -> PortalProfilePayload:
    return PortalProfilePayload(
        display_name=profile.display_name,
        telegram_username=profile.telegram_username,
    )


def _access_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired portal credential",
    )


def _login_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Telegram portal login is not configured",
    )
