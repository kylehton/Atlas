from importlib.resources import files
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from atlas.integrations.google.contracts import GoogleCapabilityName
from atlas.integrations.google.service import (
    GoogleAccountAlreadyLinked,
    GoogleAccountMismatch,
    GoogleAuthorizationRejected,
    GoogleConnectionBusy,
    GoogleConnectionDataError,
    GoogleConnectionService,
    GoogleConnectionSnapshot,
    GoogleConnectionUnavailable,
    GoogleCredentialChanged,
    GoogleDisconnectResult,
)
from atlas.onboarding.auth_routes import access_denied, portal_session_token
from atlas.onboarding.service import SettingsAccessDenied

router = APIRouter(prefix="/connections", tags=["connections"])

GOOGLE_OAUTH_BINDING_COOKIE = "atlas_google_oauth_binding"
GOOGLE_OAUTH_CALLBACK_PATH = "/connections/google/callback"


class GoogleAuthorizationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: GoogleCapabilityName


class GoogleAuthorizationResponse(BaseModel):
    authorization_url: str


class GoogleConnectionPayload(BaseModel):
    status: Literal[
        "connected",
        "degraded",
        "reauthorization_required",
        "disconnecting",
        "disconnected",
    ]
    account_email: str | None
    gmail_connected: bool
    calendar_connected: bool


class GoogleDisconnectPayload(BaseModel):
    connection: GoogleConnectionPayload
    provider_revoked: bool


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def connections_page() -> HTMLResponse:
    response = HTMLResponse(_asset_text("connections.html"))
    _add_page_security_headers(response)
    return response


@router.get("/assets/connections.css", include_in_schema=False)
def connections_styles() -> Response:
    return Response(_asset_text("connections.css"), media_type="text/css")


@router.get("/assets/connections.js", include_in_schema=False)
def connections_script() -> Response:
    return Response(_asset_text("connections.js"), media_type="text/javascript")


@router.get("/google")
def get_google_connection(request: Request, response: Response) -> GoogleConnectionPayload:
    service: GoogleConnectionService = request.app.state.google_connection_service
    try:
        connection = service.get_connection(portal_session_token(request))
    except SettingsAccessDenied:
        raise access_denied() from None
    except GoogleConnectionDataError:
        raise _connection_data_error() from None
    response.headers["Cache-Control"] = "no-store"
    return _connection_payload(connection)


@router.post("/google/authorize")
def authorize_google_connection(
    payload: GoogleAuthorizationPayload,
    request: Request,
    response: Response,
) -> GoogleAuthorizationResponse:
    service: GoogleConnectionService = request.app.state.google_connection_service
    try:
        authorization = service.begin_authorization(
            portal_session_token(request),
            payload.capability,
        )
    except SettingsAccessDenied:
        raise access_denied() from None
    except GoogleConnectionUnavailable:
        raise _google_unavailable() from None
    except GoogleConnectionBusy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google disconnect is already in progress",
        ) from None
    response.set_cookie(
        GOOGLE_OAUTH_BINDING_COOKIE,
        authorization.browser_binding,
        expires=authorization.expires_at,
        httponly=True,
        secure=request.app.state.settings.environment != "development",
        samesite="lax",
        path=GOOGLE_OAUTH_CALLBACK_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return GoogleAuthorizationResponse(authorization_url=authorization.authorization_url)


@router.get("/google/callback", response_model=None)
async def complete_google_connection(
    request: Request,
    state_value: Annotated[str, Query(alias="state", min_length=32, max_length=2_048)],
    code: Annotated[str | None, Query(min_length=1, max_length=4_096)] = None,
    error: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> Response:
    """Complete OAuth only when state and its one-time initiating-browser cookie agree."""

    service: GoogleConnectionService = request.app.state.google_connection_service
    browser_binding = request.cookies.get(GOOGLE_OAUTH_BINDING_COOKIE)
    if browser_binding is None:
        return _authorization_rejected_response(request)
    try:
        if error is not None:
            service.cancel_authorization(state_value, browser_binding=browser_binding)
            result = "cancelled" if error == "access_denied" else "failed"
            response = _connections_redirect(result)
            _clear_oauth_binding(response, request)
            return response
        if code is None:
            raise GoogleAuthorizationRejected
        await service.complete_authorization(
            state=state_value,
            code=code,
            browser_binding=browser_binding,
        )
    except GoogleAccountMismatch:
        response = _connections_redirect("account-mismatch")
        _clear_oauth_binding(response, request)
        return response
    except GoogleAccountAlreadyLinked:
        response = _connections_redirect("account-unavailable")
        _clear_oauth_binding(response, request)
        return response
    except GoogleCredentialChanged:
        response = _connections_redirect("changed")
        _clear_oauth_binding(response, request)
        return response
    except GoogleAuthorizationRejected:
        return _authorization_rejected_response(request)
    except GoogleConnectionUnavailable:
        raise _google_unavailable() from None
    except GoogleConnectionDataError:
        raise _connection_data_error() from None
    response = _connections_redirect("connected")
    _clear_oauth_binding(response, request)
    return response


@router.delete("/google")
async def disconnect_google_connection(request: Request) -> GoogleDisconnectPayload:
    service: GoogleConnectionService = request.app.state.google_connection_service
    try:
        result = await service.disconnect(portal_session_token(request))
    except SettingsAccessDenied:
        raise access_denied() from None
    except GoogleConnectionDataError:
        raise _connection_data_error() from None
    except GoogleCredentialChanged:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google connection changed; refresh and try again",
        ) from None
    return _disconnect_payload(result)


def _connection_payload(connection: GoogleConnectionSnapshot) -> GoogleConnectionPayload:
    return GoogleConnectionPayload(
        status=connection.status.value,
        account_email=connection.account_email,
        gmail_connected=connection.gmail_connected,
        calendar_connected=connection.calendar_connected,
    )


def _disconnect_payload(result: GoogleDisconnectResult) -> GoogleDisconnectPayload:
    return GoogleDisconnectPayload(
        connection=_connection_payload(result.connection),
        provider_revoked=result.provider_revoked,
    )


def _connections_redirect(result: str) -> RedirectResponse:
    response = RedirectResponse(
        f"/connections?google={result}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _authorization_rejected_response(request: Request) -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid, expired, or incomplete Google authorization"},
    )
    _clear_oauth_binding(response, request)
    response.headers["Cache-Control"] = "no-store"
    return response


def _clear_oauth_binding(response: Response, request: Request) -> None:
    response.delete_cookie(
        GOOGLE_OAUTH_BINDING_COOKIE,
        path=GOOGLE_OAUTH_CALLBACK_PATH,
        secure=request.app.state.settings.environment != "development",
        httponly=True,
        samesite="lax",
    )


def _asset_text(name: str) -> str:
    return files("atlas.connections").joinpath(name).read_text(encoding="utf-8")


def _add_page_security_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; connect-src 'self'; img-src 'self'; "
        "script-src 'self'; style-src 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _google_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Google connections are not configured",
    )


def _connection_data_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Google connection data is inconsistent",
    )
