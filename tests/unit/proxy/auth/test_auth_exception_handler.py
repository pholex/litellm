import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request, status
from prisma.engine.errors import (
    BinaryNotFoundError,
    EngineConnectionError,
    EngineRequestError,
    MismatchedVersionsError,
)
from prisma.errors import (
    ClientNotConnectedError,
    DataError,
    ForeignKeyViolationError,
    HTTPClientClosedError,
    MissingRequiredValueError,
    PrismaError,
    RawQueryError,
    RecordNotFoundError,
    TableNotFoundError,
    UniqueViolationError,
)

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import (
    ModelAccessDeniedProxyException,
    ProxyErrorTypes,
    ProxyException,
    UserAPIKeyAuth,
)
from litellm.proxy.auth.auth_exception_handler import (
    UserAPIKeyAuthExceptionHandler,
    _as_proxy_exception,
)
from litellm.proxy.auth.model_access_denied import ModelAccessDeniedHTTPException


class _EngineHttp500:
    """The response half of an EngineRequestError: the query engine answered a request with HTTP 500."""

    status = 500


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "db_error",
    [
        pytest.param(httpx.ConnectError("All connection attempts failed"), id="ConnectError"),
        pytest.param(httpx.ReadError("read failed"), id="ReadError"),
        pytest.param(httpx.ReadTimeout("timed out"), id="ReadTimeout"),
        pytest.param(EngineConnectionError(), id="EngineConnectionError"),
    ],
)
async def test_handle_authentication_error_db_unavailable_connectivity(db_error):
    """A database that is temporarily unreachable triggers the HA fallback.

    These are the failures a real outage actually produces: the query engine is
    a local HTTP server, so an unreachable database surfaces as a transport
    error against it."""
    handler = UserAPIKeyAuthExceptionHandler()

    mock_request = MagicMock()
    with patch(
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": True},
    ):
        result = await handler.handle_authentication_error(
            db_error,
            mock_request,
            {},
            "/test",
            None,
            "test-key",
        )
        assert result.key_name == "failed-to-connect-to-db"
        assert result.token == "failed-to-connect-to-db"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prisma_error",
    [
        pytest.param(
            BinaryNotFoundError("query engine binary not found"),
            id="BinaryNotFoundError",
        ),
        pytest.param(MismatchedVersionsError(expected="1", got="2"), id="MismatchedVersionsError"),
        pytest.param(HTTPClientClosedError(), id="HTTPClientClosedError"),
        pytest.param(ClientNotConnectedError(), id="ClientNotConnectedError"),
        pytest.param(PrismaError(), id="bare_PrismaError"),
    ],
)
async def test_handle_authentication_error_permanent_fault_gets_no_fallback_identity(
    prisma_error,
):
    """A fault that cannot resolve on its own must not mint a fallback identity,
    even with ``allow_requests_on_db_unavailable`` enabled.

    That setting trades verification for availability on the assumption the
    database returns. When it never will, the trade buys nothing and the proxy
    would keep admitting callers it cannot verify for as long as it runs, so the
    fault has to reach the caller instead.

    It must still reach them as a service failure. Denying the fallback is not
    licence to report a database fault as a rejected credential, which would
    send an operator hunting a key problem that does not exist."""
    handler = UserAPIKeyAuthExceptionHandler()

    mock_request = MagicMock()
    with patch(
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": True},
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(
                prisma_error,
                mock_request,
                {},
                "/test",
                None,
                "test-key",
            )

    assert exc_info.value.type == ProxyErrorTypes.no_db_connection
    assert exc_info.value.code == str(status.HTTP_503_SERVICE_UNAVAILABLE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prisma_error",
    [
        pytest.param(
            BinaryNotFoundError("query engine binary not found"),
            id="BinaryNotFoundError",
        ),
        pytest.param(MismatchedVersionsError(expected="1", got="2"), id="MismatchedVersionsError"),
        pytest.param(
            EngineRequestError(_EngineHttp500(), "query engine crashed"),
            id="EngineRequestError",
        ),
        pytest.param(PrismaError(), id="bare_PrismaError"),
    ],
)
async def test_handle_authentication_error_permanent_fault_503_is_not_worded_as_transient(
    prisma_error,
):
    """The 503 for a fault that never heals must not say the database is
    "temporarily unreachable" and ask the caller to retry. The status is right
    (the service is at fault) but that wording sends the operator to wait out an
    outage that is not one, so the message has to say retrying will not help and
    name the engine fault."""
    handler = UserAPIKeyAuthExceptionHandler()

    with patch(  # test-quality-ok: the handler reads general_settings off the proxy module, no injection seam
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": False},
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(prisma_error, MagicMock(), {}, "/test", None, "test-key")

    assert exc_info.value.code == str(status.HTTP_503_SERVICE_UNAVAILABLE)
    assert exc_info.value.type == ProxyErrorTypes.no_db_connection
    assert "temporarily unreachable" not in exc_info.value.message
    assert "retry shortly" not in exc_info.value.message.lower()
    assert "will not clear by retrying" in exc_info.value.message
    assert type(prisma_error).__name__ in exc_info.value.message


@pytest.mark.asyncio
async def test_handle_authentication_error_transport_error_raised_over_a_permanent_fault_names_the_fault():
    """A reconnect attempt that fails because the engine binary is missing surfaces as a transport
    error with the BinaryNotFoundError as __context__. The response must describe the binary, which is
    what keeps the database down, rather than promise the connection will come back."""
    try:
        raise BinaryNotFoundError("query engine binary not found")
    except BinaryNotFoundError:
        try:
            raise httpx.ConnectError("All connection attempts failed")
        except httpx.ConnectError as surfaced:
            transport_over_fault = surfaced
    handler = UserAPIKeyAuthExceptionHandler()

    with patch(  # test-quality-ok: the handler reads general_settings off the proxy module, no injection seam
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": False},
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(transport_over_fault, MagicMock(), {}, "/test", None, "k")

    assert exc_info.value.code == str(status.HTTP_503_SERVICE_UNAVAILABLE)
    assert "temporarily unreachable" not in exc_info.value.message
    assert "BinaryNotFoundError" in exc_info.value.message
    assert "will not clear by retrying" in exc_info.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "db_error",
    [
        pytest.param(httpx.ConnectError("All connection attempts failed"), id="ConnectError"),
        pytest.param(EngineConnectionError(), id="EngineConnectionError"),
        pytest.param(PrismaError("can't reach database server"), id="P1001_text"),
    ],
)
async def test_handle_authentication_error_transient_outage_503_keeps_retry_wording(
    db_error,
):
    """A genuine outage is expected to come back, so its 503 keeps telling the
    caller the database is temporarily unreachable and to retry."""
    handler = UserAPIKeyAuthExceptionHandler()

    with patch(  # test-quality-ok: the handler reads general_settings off the proxy module, no injection seam
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": False},
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(db_error, MagicMock(), {}, "/test", None, "test-key")

    assert exc_info.value.code == str(status.HTTP_503_SERVICE_UNAVAILABLE)
    assert exc_info.value.message == (
        "Service Unavailable, the authentication database is temporarily unreachable. Please retry shortly."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prisma_error",
    [
        DataError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        UniqueViolationError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        ForeignKeyViolationError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        MissingRequiredValueError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        RawQueryError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        TableNotFoundError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
        RecordNotFoundError(data={"user_facing_error": {"meta": {"table": "test_table"}}}),
    ],
)
async def test_handle_authentication_error_data_layer_errors_do_not_fall_back(
    prisma_error,
):
    """Known data-layer PrismaError subclasses (UniqueViolation,
    RecordNotFound, etc.) mean the DB IS reachable — they must propagate
    instead of triggering the HA fallback, which would grant the
    restricted INTERNAL_USER token to a request that should have
    returned 401."""
    handler = UserAPIKeyAuthExceptionHandler()

    mock_request = MagicMock()
    with patch(
        "litellm.proxy.proxy_server.general_settings",
        {"allow_requests_on_db_unavailable": True},
    ):
        with pytest.raises(ProxyException):
            await handler.handle_authentication_error(
                prisma_error,
                mock_request,
                {},
                "/test",
                None,
                "test-key",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "db_error",
    [
        ConnectionError("connection refused"),
        TimeoutError("timed out"),
        asyncio.TimeoutError(),
        OSError("network is unreachable"),
        HTTPClientClosedError(),
        PrismaError("can't reach database server"),
        RawQueryError(
            data={
                "user_facing_error": {
                    "message": "cached plan must not change result type",
                    "meta": {"table": "t"},
                }
            }
        ),
    ],
)
async def test_handle_authentication_error_db_infra_error_returns_503(db_error):
    """Regression for the outage where valid keys got 401 for 4 hours: an
    infrastructure-level DB failure during auth must surface as 503 (the DB
    could not confirm the key), never as 401 ("Invalid API key")."""
    handler = UserAPIKeyAuthExceptionHandler()

    with (
        patch(
            "litellm.proxy.proxy_server.proxy_logging_obj.post_call_failure_hook",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "litellm.proxy.proxy_server.general_settings",
            {"allow_requests_on_db_unavailable": False},
        ),
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(
                db_error,
                MagicMock(),
                {},
                "/v1/chat/completions",
                None,
                "sk-valid-but-db-down",
            )

    assert int(exc_info.value.code) == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.type == ProxyErrorTypes.no_db_connection
    assert "Invalid API key" not in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_handle_authentication_error_prisma_engine_teardown_returns_503():
    """Regression for the first-request-of-an-outage edge case: at the instant
    the DB socket drops, the prisma query engine returns a malformed error
    payload and prisma-client-py crashes with a bare
    ``AttributeError: 'NoneType' object has no attribute 'get'`` before it can
    raise P1001. That AttributeError reached auth and fell through to 401. It
    must surface as 503 like every other infra failure during the outage."""
    from prisma.engine import utils as prisma_engine_utils

    malformed_payload = [
        {
            "error": "Can't reach database server",
            "user_facing_error": {
                "error_code": "P1001",
                "message": "Can't reach database server at `localhost`:`5503`",
                "meta": None,
            },
        }
    ]
    try:
        prisma_engine_utils.handle_response_errors(None, malformed_payload)
        raise AssertionError("expected prisma to raise AttributeError")
    except AttributeError as e:
        teardown_error = e

    handler = UserAPIKeyAuthExceptionHandler()

    with (
        patch(
            "litellm.proxy.proxy_server.proxy_logging_obj.post_call_failure_hook",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "litellm.proxy.proxy_server.general_settings",
            {"allow_requests_on_db_unavailable": False},
        ),
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(
                teardown_error,
                MagicMock(),
                {},
                "/v1/chat/completions",
                None,
                "sk-valid-but-db-down",
            )

    assert int(exc_info.value.code) == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.type == ProxyErrorTypes.no_db_connection
    assert "Invalid API key" not in str(exc_info.value.message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth_error",
    [
        # DB returned no row -> get_key_object raises this exact 401.
        ProxyException(
            message="Authentication Error, Invalid proxy server token passed.",
            type=ProxyErrorTypes.token_not_found_in_db,
            param="key",
            code=status.HTTP_401_UNAUTHORIZED,
        ),
        # A bare auth failure raised as a plain Exception (e.g. master-key-only
        # route) must keep returning 401, not get reclassified as 503.
        Exception("Invalid proxy server token passed"),
    ],
)
async def test_handle_authentication_error_genuine_auth_failure_stays_401(auth_error):
    """Guard against the 503 conversion being too broad: a genuine auth
    failure (missing key / wrong key) must still be 401."""
    handler = UserAPIKeyAuthExceptionHandler()

    with (
        patch(
            "litellm.proxy.proxy_server.proxy_logging_obj.post_call_failure_hook",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "litellm.proxy.proxy_server.general_settings",
            {"allow_requests_on_db_unavailable": False},
        ),
    ):
        with pytest.raises(ProxyException) as exc_info:
            await handler.handle_authentication_error(
                auth_error,
                MagicMock(),
                {},
                "/v1/chat/completions",
                None,
                "sk-bad-key",
            )

    assert int(exc_info.value.code) == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_handle_authentication_error_budget_exceeded():
    handler = UserAPIKeyAuthExceptionHandler()

    # Mock request and other dependencies
    mock_request = MagicMock()
    mock_request_data = {}
    mock_route = "/test"
    mock_span = None
    mock_api_key = "test-key"

    # Test with budget exceeded error
    from litellm.exceptions import BudgetExceededError

    budget_error = BudgetExceededError(message="Budget exceeded", current_cost=100, max_budget=100)

    with pytest.raises(ProxyException) as exc_info:
        await handler.handle_authentication_error(
            budget_error,
            mock_request,
            mock_request_data,
            mock_route,
            mock_span,
            mock_api_key,
        )

    assert exc_info.value.type == ProxyErrorTypes.budget_exceeded
    assert int(exc_info.value.code) == status.HTTP_422_UNPROCESSABLE_CONTENT


_DENIED_CLIENT_MESSAGE = (
    "The requested model 'gpt-5.6' is not available for this API key, or the model name is invalid. "
    "Check the models available to you and try again."
)


def _denied_proxy_exception() -> ModelAccessDeniedProxyException:
    return ModelAccessDeniedProxyException(
        message=_DENIED_CLIENT_MESSAGE,
        internal_message="key not allowed to access model. This key can only access models=['internal-models']. "
        "Tried to access gpt-5.6\r\nWARNING forged log line",
        type=ProxyErrorTypes.key_model_access_denied,
        param="model",
        code=status.HTTP_403_FORBIDDEN,
    )


def _denied_jwt_exception() -> ModelAccessDeniedHTTPException:
    return ModelAccessDeniedHTTPException(
        internal_message="Role=engineer not allowed to call model=gpt-5.6\r\nWARNING forged log line. "
        "Allowed models=['internal-models']",
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_DENIED_CLIENT_MESSAGE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_denial",
    [
        pytest.param(_denied_proxy_exception, id="proxy_exception"),
        pytest.param(_denied_jwt_exception, id="jwt_http_exception"),
    ],
)
async def test_handle_authentication_error_keeps_internal_message_on_model_access_denial(make_denial, caplog):
    handler = UserAPIKeyAuthExceptionHandler()
    denial = make_denial()

    with (
        patch(  # test-quality-ok: handler reads proxy_server globals at call time
            "litellm.proxy.proxy_server.proxy_logging_obj.post_call_failure_hook",
            new_callable=AsyncMock,
            return_value=None,
        ) as callback,
        patch(  # test-quality-ok: handler reads proxy_server globals at call time
            "litellm.proxy.proxy_server.general_settings",
            {"allow_requests_on_db_unavailable": False},
        ),
        caplog.at_level("WARNING", logger="LiteLLM Proxy"),
        pytest.raises(ModelAccessDeniedProxyException) as exc_info,
    ):
        await handler.handle_authentication_error(denial, MagicMock(), {}, "/v1/chat/completions", None, "sk-bad-key")

    callback.assert_not_awaited()
    assert exc_info.value.code == str(status.HTTP_403_FORBIDDEN)
    assert "internal-models" not in str(exc_info.value.message)
    assert exc_info.value.internal_message == denial.internal_message
    assert [r for r in caplog.records if r.levelname == "WARNING" and "internal-models" in r.getMessage()] == []


def test_as_proxy_exception_keeps_jwt_scope_denial_message_shape():
    detail = {"error": _DENIED_CLIENT_MESSAGE}
    denial = ModelAccessDeniedHTTPException(
        internal_message="model=gpt-5.6 not allowed. Allowed_models=['internal-models']",
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )
    plain = _as_proxy_exception(HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail))

    converted = _as_proxy_exception(denial)

    assert converted.to_dict() == plain.to_dict()
    assert converted.internal_message == denial.internal_message


@pytest.mark.parametrize(
    "auth_failure",
    [
        pytest.param(
            HTTPException(status_code=401, detail="Authentication Error, invalid key"),
            id="http_exception",
        ),
        pytest.param(Exception("Invalid proxy server token passed"), id="bare_exception"),
    ],
)
def test_auth_failure_error_body_carries_a_json_null_param(
    auth_failure: Exception,
) -> None:
    body = json.loads(json.dumps({"error": _as_proxy_exception(auth_failure).to_dict()}))

    assert body["error"]["type"] == ProxyErrorTypes.auth_error.value
    assert body["error"]["param"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, expected_status, expected_reason",
    [
        (HTTPException(401, "request-marker"), 401, "unauthorized"),
        (
            ProxyException(
                message="request-marker",
                type=ProxyErrorTypes.expired_key,
                param=None,
                code=401,
            ),
            401,
            "expired_key",
        ),
        (HTTPException(403, "request-marker"), 403, "forbidden"),
        (HTTPException(429, "request-marker"), 429, "rate_limited"),
    ],
)
async def test_auth_rejection_only_emits_fixed_log(failure, expected_status, expected_reason, monkeypatch, caplog):
    from uuid import UUID

    import litellm

    monkeypatch.setattr(litellm, "log_auth_failure_key_identity", True)
    request = Request(
        {
            "type": "http",
            "path": "/v1/responses/request-marker",
            "route": SimpleNamespace(path="/v1/responses/{response_id}"),
            "client": ("192.0.2.5", 8911),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.9"),
                (b"user-agent", b"request-marker"),
            ],
            "query_string": b"input=request-marker",
        }
    )
    identity = UserAPIKeyAuth(
        token="request-marker",
        user_id="user-123",
        team_id="team-456",
        metadata={"note": "request-marker"},
    )
    request_data = {"model": "request-marker", "metadata": {"note": "request-marker"}}
    monkeypatch.setattr(verbose_proxy_logger, "propagate", True)
    with (
        patch(
            "litellm.proxy.proxy_server.proxy_logging_obj.post_call_failure_hook",
            new_callable=AsyncMock,
        ) as callback,
        patch("httpx.AsyncClient.send", new_callable=AsyncMock) as outbound,
        caplog.at_level("WARNING", logger="LiteLLM Proxy"),
        pytest.raises(ProxyException) as result,
    ):
        await UserAPIKeyAuthExceptionHandler.handle_authentication_error(
            failure,
            request,
            request_data,
            "/v1/responses/request-marker",
            None,
            "request-marker",
            resolved_identity=identity,
        )

    callback.assert_not_awaited()
    outbound.assert_not_awaited()
    assert int(result.value.code) == expected_status
    records = [record for record in caplog.records if record.getMessage().startswith("auth_rejected ")]
    assert len(records) == 1
    assert records[0].exc_info is None
    event = json.loads(records[0].getMessage().split(" ", 1)[1])
    assert UUID(event.pop("event_id")).version == 4
    assert event == {
        "route": "/v1/responses/{response_id}",
        "client_ip": "192.0.2.5",
        "status": str(expected_status),
        "reason": expected_reason,
        "user_id": "user-123",
        "team_id": "team-456",
    }
    assert "request-marker" not in records[0].getMessage()
