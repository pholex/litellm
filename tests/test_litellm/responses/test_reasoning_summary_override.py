"""reasoning_summary_override litellm_param (tokenweave fork).

Bedrock-hosted grok-4.6 / gpt-6-astra 400 on reasoning.summary values other
than "auto"; Codex sends "detailed" by default. The override normalises the
value per deployment inside responses() before the provider handler runs.
"""
import pytest

import litellm
from litellm.responses.utils import ResponsesAPIRequestUtils


_captured: dict = {}


@pytest.fixture
def capture(monkeypatch):
    # Short-circuit right after the fork hook ran; litellm re-wraps the
    # exception on the way out, so the value is read from _captured instead.
    def _fake(local_vars):
        _captured["reasoning"] = local_vars.get("reasoning")
        raise RuntimeError("stop after hook")

    monkeypatch.setattr(
        ResponsesAPIRequestUtils, "get_requested_response_api_optional_param", staticmethod(_fake)
    )
    _captured.clear()


def _run(**kwargs):
    with pytest.raises(Exception):
        litellm.responses(
            model="openai/us.xai.grok-4.6",
            input="hi",
            api_key="sk-test",
            api_base="https://example.invalid/openai/v1",
            **kwargs,
        )
    assert "reasoning" in _captured, "hook did not reach the capture point"
    return _captured["reasoning"]


@pytest.mark.usefixtures("capture")
def test_override_replaces_summary():
    assert _run(
        reasoning={"effort": "medium", "summary": "detailed"},
        reasoning_summary_override="auto",
    ) == {"effort": "medium", "summary": "auto"}


@pytest.mark.usefixtures("capture")
def test_override_drop_removes_summary():
    assert _run(
        reasoning={"effort": "low", "summary": "concise"},
        reasoning_summary_override="drop",
    ) == {"effort": "low"}


@pytest.mark.usefixtures("capture")
def test_no_override_leaves_request_untouched():
    assert _run(reasoning={"effort": "low", "summary": "detailed"}) == {
        "effort": "low",
        "summary": "detailed",
    }


@pytest.mark.usefixtures("capture")
def test_override_ignores_requests_without_summary():
    assert _run(reasoning={"effort": "high"}, reasoning_summary_override="auto") == {"effort": "high"}
    assert _run(reasoning_summary_override="auto") is None
