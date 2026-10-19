from collections.abc import Iterator

import pytest

import litellm


@pytest.fixture
def local_model_cost_map(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Backport of the upstream tests/unit/conftest.py fixture used by the MiniMax video tests."""
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(litellm, "model_cost", litellm.get_model_cost_map(url=""))
    if hasattr(litellm.get_model_info, "cache_clear"):
        litellm.get_model_info.cache_clear()
    yield
    if hasattr(litellm.get_model_info, "cache_clear"):
        litellm.get_model_info.cache_clear()
