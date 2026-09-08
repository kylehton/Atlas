from datetime import time
from importlib.resources import files
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.onboarding.service import (
    SettingsAccessDenied,
    SettingsIdentityMismatch,
    SettingsLoginUnavailable,
    SettingsPreferences,
    SettingsService,
)
from atlas.shared.field_types import TimezoneName

SETTINGS_SESSION_COOKIE = "atlas_settings_session"

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsLoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=32, max_length=128)


class TelegramAuthorizationPayload(BaseModel):
    authorization_url: str


class SettingsPreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: TimezoneName
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    notifications_enabled: bool = True
    notifications_on_weekends: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Accept only timezone identifiers understood by the runtime timezone database."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value

    @model_validator(mode="after")
    def validate_quiet_hours_pair(self) -> "SettingsPreferencesPayload":
        """Require both quiet-hour boundaries so notification behavior is unambiguous."""

        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet_hours_start and quiet_hours_end must be set together")
        return self


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def settings_page() -> HTMLResponse:
    response = HTMLResponse(_asset_text("settings.html"))
    _add_page_security_headers(response)
    return response


@router.get("/assets/settings.css", include_in_schema=False)
def settings_styles() -> Response:
    return Response(_asset_text("settings.css"), media_type="text/css")


@router.get("/assets/settings.js", include_in_schema=False)
def settings_script() -> Response:
    return Response(_asset_text("settings.js"), media_type="text/javascript")


@router.post("/auth/telegram/start")
def start_telegram_login(
    payload: SettingsLoginPayload,
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


@router.get("/auth/telegram/callback", response_model=None)
async def complete_telegram_login(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=4_096)],
    state_value: Annotated[
        str,
        Query(alias="state", min_length=32, max_length=128),
    ],
) -> RedirectResponse:
    """Create a settings session only when Telegram matches the link's expected user."""

    service: SettingsService = request.app.state.settings_service
    try:
        settings_session = await service.complete_telegram_login(
            code=code,
            state=state_value,
        )
    except SettingsIdentityMismatch:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram account does not match this settings request",
        ) from None
    except SettingsAccessDenied:
        raise _access_denied() from None
    except SettingsLoginUnavailable:
        raise _login_unavailable() from None

    response = RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SETTINGS_SESSION_COOKIE,
        settings_session.value,
        expires=settings_session.expires_at,
        httponly=True,
        secure=request.app.state.settings.environment != "development",
        samesite="strict",
        path="/settings",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/preferences")
def get_settings_preferences(request: Request, response: Response) -> SettingsPreferencesPayload:
    service: SettingsService = request.app.state.settings_service
    try:
        preferences = service.get_preferences(_session_token(request))
    except SettingsAccessDenied:
        raise _access_denied() from None
    response.headers["Cache-Control"] = "no-store"
    return _preference_payload(preferences)


@router.put("/preferences")
def update_settings_preferences(
    payload: SettingsPreferencesPayload,
    request: Request,
    response: Response,
) -> SettingsPreferencesPayload:
    service: SettingsService = request.app.state.settings_service
    try:
        preferences = service.update_preferences(
            _session_token(request),
            timezone=payload.timezone,
            quiet_hours_start=payload.quiet_hours_start,
            quiet_hours_end=payload.quiet_hours_end,
            notifications_enabled=payload.notifications_enabled,
            notifications_on_weekends=payload.notifications_on_weekends,
        )
    except SettingsAccessDenied:
        raise _access_denied() from None
    response.headers["Cache-Control"] = "no-store"
    return _preference_payload(preferences)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_settings_session(request: Request) -> Response:
    """Revoke the server-side session and remove its browser cookie."""

    service: SettingsService = request.app.state.settings_service
    session_token = request.cookies.get(SETTINGS_SESSION_COOKIE)
    if session_token is not None:
        service.revoke_session(session_token)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SETTINGS_SESSION_COOKIE, path="/settings", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return response


def _session_token(request: Request) -> str:
    session_token = request.cookies.get(SETTINGS_SESSION_COOKIE)
    if session_token is None:
        raise SettingsAccessDenied
    return session_token


def _preference_payload(preferences: SettingsPreferences) -> SettingsPreferencesPayload:
    return SettingsPreferencesPayload(
        timezone=preferences.timezone,
        quiet_hours_start=preferences.quiet_hours_start,
        quiet_hours_end=preferences.quiet_hours_end,
        notifications_enabled=preferences.notifications_enabled,
        notifications_on_weekends=preferences.notifications_on_weekends,
    )


def _access_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired settings credential",
    )


def _login_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Telegram settings login is not configured",
    )


def _asset_text(name: str) -> str:
    return files("atlas.onboarding").joinpath(name).read_text(encoding="utf-8")


def _add_page_security_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; connect-src 'self'; img-src 'self'; "
        "script-src 'self'; style-src 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
