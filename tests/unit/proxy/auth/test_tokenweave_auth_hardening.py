"""TokenWeave auth hardening for the proxy master key and management writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

import litellm.proxy.proxy_server as proxy_server
from litellm.constants import LITELLM_PROXY_MASTER_KEY_ALIAS
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
