"""Blocked environment keys stay out of the process environment whatever their source."""

import os
from unittest.mock import patch

import pytest

from litellm.proxy.proxy_server import ProxyConfig

_BLOCKED_IN_DB = {
    "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES": "1",
    "LD_PRELOAD": "/tmp/evil.so",
    # Stored in lower case: the DB overlay also exports the upper-cased key.
    "google_application_credentials": "/tmp/attacker.json",
}
_ALLOWED = {"LANGFUSE_PUBLIC_KEY": "pk-test", "custom_lower_key": "v"}


@pytest.fixture
def clean_env(monkeypatch):
    for key in [*_BLOCKED_IN_DB, *_ALLOWED]:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.upper(), raising=False)


@pytest.fixture
def plaintext_db_values():
    with patch(
        "litellm.proxy.proxy_server.decrypt_value_helper",
        side_effect=lambda value, key, return_original_value=False: value,
    ):
        yield


def _assert_blocked_keys_unset():
    for key in _BLOCKED_IN_DB:
        assert os.environ.get(key) is None
        assert os.environ.get(key.upper()) is None


@pytest.mark.usefixtures("clean_env", "plaintext_db_values")
def test_db_environment_variables_skip_blocked_keys():
    proxy_config = ProxyConfig()
    proxy_config._apply_db_environment_variables(
        {**_BLOCKED_IN_DB, **_ALLOWED}, revision=None, environment_before_db={}, read=1
    )

    _assert_blocked_keys_unset()
    assert not any(ProxyConfig._is_blocked_env_key(key) for key in proxy_config._db_environment_keys)
    # Ordinary keys still apply, including the upper-case export.
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test"
    assert os.environ["CUSTOM_LOWER_KEY"] == "v"
    assert proxy_config._db_environment_keys == {"LANGFUSE_PUBLIC_KEY", "custom_lower_key", "CUSTOM_LOWER_KEY"}


@pytest.mark.usefixtures("clean_env", "plaintext_db_values")
def test_decrypt_and_set_db_env_variables_skips_blocked_keys():
    """Also the path the SSO settings row takes at startup."""
    applied = ProxyConfig().decrypt_and_set_db_env_variables({**_BLOCKED_IN_DB, **_ALLOWED})

    _assert_blocked_keys_unset()
    assert applied == _ALLOWED


@pytest.mark.usefixtures("clean_env")
def test_config_file_environment_variables_skip_blocked_keys_in_any_case():
    ProxyConfig()._load_environment_variables(config={"environment_variables": {**_BLOCKED_IN_DB, **_ALLOWED}})

    _assert_blocked_keys_unset()
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test"


@pytest.mark.parametrize(
    ("key", "blocked"),
    [
        ("LD_PRELOAD", True),
        ("ld_preload", True),
        ("no_proxy", True),
        ("Google_Application_Credentials", True),
        ("LANGFUSE_PUBLIC_KEY", False),
        (None, False),
        (1, False),
    ],
)
def test_is_blocked_env_key(key, blocked):
    assert ProxyConfig._is_blocked_env_key(key) is blocked
