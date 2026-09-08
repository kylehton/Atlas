from datetime import time
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from atlas.onboarding.auth_routes import access_denied, portal_session_token
from atlas.onboarding.service import (
    SettingsAccessDenied,
    SettingsPreferences,
    SettingsService,
)
from atlas.shared.field_types import ShortText, TimezoneName
from atlas.shared.timezones import SUPPORTED_TIMEZONES, TIMEZONE_OPTIONS

router = APIRouter(prefix="/notifications", tags=["notification settings"])


class TimezoneOptionPayload(BaseModel):
    value: TimezoneName
    label: ShortText


class TimezoneOptionsPayload(BaseModel):
    timezones: tuple[TimezoneOptionPayload, ...]


class NotificationPreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: TimezoneName
    notification_window_start: time | None = None
    notification_window_end: time | None = None
    notifications_enabled: bool = True
    notifications_on_weekends: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Accept only Atlas-supported identifiers present in the runtime timezone database."""

        if value not in SUPPORTED_TIMEZONES:
            raise ValueError("timezone is not currently supported by Atlas")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value

    @model_validator(mode="after")
    def validate_notification_window_pair(self) -> "NotificationPreferencesPayload":
        """Require both window boundaries so notification behavior is unambiguous."""

        if (self.notification_window_start is None) != (self.notification_window_end is None):
            raise ValueError(
                "notification_window_start and notification_window_end must be set together"
            )
        return self


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def notification_settings_page() -> HTMLResponse:
    response = HTMLResponse(_asset_text("notifications.html"))
    _add_page_security_headers(response)
    return response


@router.get("/assets/notifications.css", include_in_schema=False)
def notification_settings_styles() -> Response:
    return Response(_asset_text("notifications.css"), media_type="text/css")


@router.get("/assets/notifications.js", include_in_schema=False)
def notification_settings_script() -> Response:
    return Response(_asset_text("notifications.js"), media_type="text/javascript")


@router.get("/timezones")
def get_timezone_options(response: Response) -> TimezoneOptionsPayload:
    """Return the curated IANA timezone names accepted by notification settings."""

    response.headers["Cache-Control"] = "public, max-age=86400"
    return TimezoneOptionsPayload(
        timezones=tuple(
            TimezoneOptionPayload(value=value, label=label) for value, label in TIMEZONE_OPTIONS
        )
    )


@router.get("/preferences")
def get_notification_preferences(
    request: Request,
    response: Response,
) -> NotificationPreferencesPayload:
    service: SettingsService = request.app.state.settings_service
    try:
        preferences = service.get_preferences(portal_session_token(request))
    except SettingsAccessDenied:
        raise access_denied() from None
    response.headers["Cache-Control"] = "no-store"
    return _preference_payload(preferences)


@router.put("/preferences")
def update_notification_preferences(
    payload: NotificationPreferencesPayload,
    request: Request,
    response: Response,
) -> NotificationPreferencesPayload:
    service: SettingsService = request.app.state.settings_service
    try:
        preferences = service.update_preferences(
            portal_session_token(request),
            timezone=payload.timezone,
            notification_window_start=payload.notification_window_start,
            notification_window_end=payload.notification_window_end,
            notifications_enabled=payload.notifications_enabled,
            notifications_on_weekends=payload.notifications_on_weekends,
        )
    except SettingsAccessDenied:
        raise access_denied() from None
    response.headers["Cache-Control"] = "no-store"
    return _preference_payload(preferences)


def _preference_payload(preferences: SettingsPreferences) -> NotificationPreferencesPayload:
    return NotificationPreferencesPayload(
        timezone=preferences.timezone,
        notification_window_start=preferences.notification_window_start,
        notification_window_end=preferences.notification_window_end,
        notifications_enabled=preferences.notifications_enabled,
        notifications_on_weekends=preferences.notifications_on_weekends,
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
