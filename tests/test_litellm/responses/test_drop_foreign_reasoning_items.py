"""drop_foreign_reasoning_items litellm_param (tokenweave fork).

Codex resends prior encrypted reasoning items each turn; OpenAI-family models
on Bedrock reject blobs produced by another deployment. The hook keeps only
items tagged (encrypted_content_affinity) with this deployment's model_id.
"""
import pytest

import litellm
from litellm.responses.utils import ResponsesAPIRequestUtils

OWN = "deploy-own"
OTHER = "deploy-other"
_captured: dict = {}


def _wrap(model_id: str, blob: str = "rsn_blob") -> str:
    return ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id(blob, model_id)


@pytest.fixture
def capture(monkeypatch):
    def _fake(local_vars):
        _captured["input"] = local_vars.get("input")
        raise RuntimeError("stop after hook")

    monkeypatch.setattr(
        ResponsesAPIRequestUtils, "get_requested_response_api_optional_param", staticmethod(_fake)
    )
    _captured.clear()


def _run(input_items, **kwargs):
    with pytest.raises(Exception):
        litellm.responses(
            model="openai/us.openai.gpt-6-astra",
            input=input_items,
            api_key="sk-test",
            api_base="https://example.invalid/openai/v1",
            **kwargs,
        )
    assert "input" in _captured, "hook did not reach the capture point"
    return _captured["input"]


def _types(items):
    return [(i.get("type"), i.get("id")) for i in items]


USER = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ping"}]}
ASSISTANT = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "pong"}]}


@pytest.mark.usefixtures("capture")
def test_keeps_own_drops_foreign_and_untagged():
    items = [
        USER,
        {"type": "reasoning", "id": "rs_own", "summary": [], "encrypted_content": _wrap(OWN)},
        {"type": "reasoning", "id": "rs_other", "summary": [], "encrypted_content": _wrap(OTHER)},
        {"type": "reasoning", "id": "rs_untagged", "summary": [], "encrypted_content": "rsn_raw"},
        {"type": "reasoning", "id": "rs_no_blob", "summary": [{"type": "summary_text", "text": "x"}]},
        ASSISTANT,
    ]
    out = _run(items, drop_foreign_reasoning_items=True, model_info={"id": OWN})
    assert _types(out) == [
        ("message", None),
        ("reasoning", "rs_own"),
        ("reasoning", "rs_no_blob"),
        ("message", None),
    ]
    # kept item is passed through untouched (unwrapping happens later in the pipeline)
    assert out[1]["encrypted_content"] == _wrap(OWN)


@pytest.mark.usefixtures("capture")
def test_origin_from_encoded_item_id():
    enc_id = ResponsesAPIRequestUtils._build_encrypted_item_id(OWN, "rs_1")
    other_id = ResponsesAPIRequestUtils._build_encrypted_item_id(OTHER, "rs_2")
    items = [
        USER,
        {"type": "reasoning", "id": enc_id, "summary": [], "encrypted_content": "rsn_raw"},
        {"type": "reasoning", "id": other_id, "summary": [], "encrypted_content": "rsn_raw"},
    ]
    out = _run(items, drop_foreign_reasoning_items=True, model_info={"id": OWN})
    assert [i.get("id") for i in out if i.get("type") == "reasoning"] == [enc_id]


@pytest.mark.usefixtures("capture")
def test_flag_off_leaves_input_untouched():
    items = [USER, {"type": "reasoning", "id": "rs_other", "summary": [], "encrypted_content": _wrap(OTHER)}]
    out = _run(items, model_info={"id": OWN})
    assert _types(out) == [("message", None), ("reasoning", "rs_other")]


@pytest.mark.usefixtures("capture")
def test_string_input_untouched():
    assert _run("hi", drop_foreign_reasoning_items=True, model_info={"id": OWN}) == "hi"
