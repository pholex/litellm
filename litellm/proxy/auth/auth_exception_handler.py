"""
Handles Authentication Errors
"""

import json
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

from fastapi import HTTPException, Request, status

import litellm
from litellm._logging import verbose_proxy_logger, verbose_proxy_stdout_logger
from litellm.proxy._types import (
    LitellmUserRoles,
    ModelAccessDeniedProxyException,
    ProxyErrorTypes,
    ProxyException,
    UserAPIKeyAuth,
)
from litellm.proxy.auth.auth_utils import (  # noqa: F401  # legacy module exports
    _get_request_ip_address,  # pyright: ignore[reportPrivateUsage,reportUnusedImport]  # backwards-compatible package export
    is_invalid_virtual_key_error,
    mark_invalid_virtual_key_error,
)
from litellm.proxy.auth.model_access_denied import ModelAccessDeniedHTTPException
from litellm.proxy.common_utils.openai_error_payload import openai_error_param
from litellm.proxy.db.exception_handler import PrismaDBExceptionHandler
from litellm.types.services import ServiceTypes

# Sentinel user_id for the synthetic UserAPIKeyAuth issued during a DB
# outage when allow_requests_on_db_unavailable is True. Downstream
# enforcement can key off this value; it must never collide with a real
# user_id.
DB_UNAVAILABLE_FALLBACK_USER_ID: Final = "__db_unavailable_fallback__"

if TYPE_CHECKING:
    from opentelemetry.trace import Span as _Span

    Span = _Span
else:
    Span = Any


def _as_proxy_exception(e: Exception) -> ProxyException:
    """Convert an authentication failure into the ProxyException the client receives."""
    if isinstance(e, litellm.BudgetExceededError):
        return ProxyException(
            message=e.message,
            type=ProxyErrorTypes.budget_exceeded,
            param=None,
            code=getattr(e, "status_code", status.HTTP_429_TOO_MANY_REQUESTS),
        )
    if isinstance(e, ModelAccessDeniedHTTPException):
        return ModelAccessDeniedProxyException(
            message=str(e.detail),
            internal_message=e.internal_message,
            type=ProxyErrorTypes.auth_error,
            param=None,
            code=e.status_code,
        )
    if isinstance(e, HTTPException):
        return ProxyException(
            message=getattr(e, "detail", f"Authentication Error({e})"),
            type=ProxyErrorTypes.auth_error,
            param=openai_error_param(e),
            code=getattr(e, "status_code", status.HTTP_401_UNAUTHORIZED),
        )
    if isinstance(e, ProxyException):
        return e
    if PrismaDBExceptionHandler.is_database_service_unavailable_error(e):
        return PrismaDBExceptionHandler.service_unavailable_proxy_exception(e)
    return ProxyException(
        message="Authentication Error, " + str(e),
        type=ProxyErrorTypes.auth_error,
        param=openai_error_param(e),
        code=status.HTTP_401_UNAUTHORIZED,
    )


def _log_auth_rejection(
    request: Request,
    error: ProxyException,
    identity: UserAPIKeyAuth | None,
) -> None:
    matched_route = getattr(request.scope.get("route"), "path", None)
    client = request.client
    client_ip = client.host if client is not None else None
    try:
        peer = str(ip_address(client_ip)) if isinstance(client_ip, str) else None
    except ValueError:
        peer = None

    response_status = str(error.code)
    reason = (
        "expired_key"
        if error.type == ProxyErrorTypes.expired_key
        else "budget_exceeded"
        if error.type == ProxyErrorTypes.budget_exceeded
        else {
            "401": "unauthorized",
            "403": "forbidden",
            "429": "rate_limited",
            "503": "service_unavailable",
        }.get(response_status, "auth_failed")
    )
    fields = {
        "event_id": uuid4().hex,
        "route": matched_route if isinstance(matched_route, str) else None,
        "client_ip": peer,
        "status": response_status,
        "reason": reason,
        "user_id": identity.user_id[:128] if identity is not None and identity.user_id is not None else None,
        "team_id": identity.team_id[:128] if identity is not None and identity.team_id is not None else None,
    }
    if response_status == "401":
        verbose_proxy_stdout_logger.warning("auth_rejected %s", json.dumps(fields))
    else:
        verbose_proxy_logger.error("auth_rejected %s", json.dumps(fields))


class UserAPIKeyAuthExceptionHandler:
    @staticmethod
    async def handle_authentication_error(
        e: Exception,
        request: Request,
        request_data: object,
        route: str,
        parent_otel_span: Span | None,
        api_key: str,
        resolved_identity: UserAPIKeyAuth | None = None,
        dispatched_call_type: str | None = None,
    ) -> UserAPIKeyAuth:
        """
        Handles Connection Errors when reading a Virtual Key from LiteLLM DB
        Use this if you don't want failed DB queries to block LLM API reqiests

        Reliability scenarios this covers:
        - DB is down and having an outage
        - Unable to read / recover a key from the DB

        Returns:
            - UserAPIKeyAuth: If general_settings.allow_requests_on_db_unavailable is True

        Raises:
            - Original Exception in all other cases
        """
        from litellm.proxy.proxy_server import (
            proxy_logging_obj,
        )

        if (
            PrismaDBExceptionHandler.should_allow_request_on_db_unavailable()
            and PrismaDBExceptionHandler.is_database_connection_error(e)
        ):
            # log this as a DB failure on prometheus
            proxy_logging_obj.service_logging_obj.service_failure_hook(
                service=ServiceTypes.DB,
                call_type="get_key_object",
                error=e,
                duration=0.0,
            )

            # Non-admin restricted token so a DB outage cannot escalate
            # an anonymous caller to proxy-admin privileges.
            verbose_proxy_logger.warning(
                "Auth: DB unavailable — issuing restricted INTERNAL_USER "
                "fallback token (allow_requests_on_db_unavailable=True)"
            )
            return UserAPIKeyAuth(
                key_name="failed-to-connect-to-db",
                token="failed-to-connect-to-db",
                user_id=DB_UNAVAILABLE_FALLBACK_USER_ID,
                user_role=LitellmUserRoles.INTERNAL_USER,
                request_route=route,
            )
        final_exception: Final = mark_invalid_virtual_key_error(_as_proxy_exception(e), is_invalid_virtual_key_error(e))
        identity: Final = resolved_identity if litellm.log_auth_failure_key_identity else None
        # A rejected request must never reach configurable logging integrations.
        _log_auth_rejection(request, final_exception, identity)
        raise final_exception

    _handle_authentication_error = handle_authentication_error
