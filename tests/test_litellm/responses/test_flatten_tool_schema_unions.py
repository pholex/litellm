"""flatten_tool_schema_unions litellm_param (tokenweave fork).

Bedrock-hosted grok-4.7 rejects requests whose tool schemas nest a oneOf inside
a oneOf (after $ref resolution) — Codex's automation_update tool does. The
helper inlines local refs and splices nested unions into their parent.
"""
import copy

import pytest

import litellm
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.utils import _flatten_json_schema_unions, flatten_tool_schema_unions

# Trimmed shape of Codex's mcp__codex_app.automation_update parameters.
NESTED = {
    "type": "object",
    "properties": {},
    "oneOf": [
        {"$ref": "#/$defs/view"},
        {"$ref": "#/$defs/create"},
        {"$ref": "#/$defs/delete"},
    ],
    "$defs": {
        "id": {"$ref": "#/$defs/str"},
        "str": {"type": "string"},
        "view": {
            "type": "object",
            "properties": {"id": {"$ref": "#/$defs/id"}, "mode": {"type": "string", "enum": ["view"]}},
            "required": ["mode", "id"],
        },
        "create": {
            "oneOf": [
                {"type": "object", "properties": {"kind": {"type": "string", "enum": ["cron"]}}},
                {"type": "object", "properties": {"kind": {"type": "string", "enum": ["heartbeat"]}}},
            ]
        },
        "delete": {
            "type": "object",
            "properties": {"id": {"$ref": "#/$defs/id", "type": "string"}, "mode": {"type": "string", "enum": ["delete"]}},
        },
    },
}


def _has_key(node, key):
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


def _nested_union(node):
    if isinstance(node, dict):
        for k in ("oneOf", "anyOf"):
            for v in node.get(k) or []:
                if isinstance(v, dict) and set(v) - {"description", "title"} in ({"oneOf"}, {"anyOf"}):
                    return True
        return any(_nested_union(v) for v in node.values())
    if isinstance(node, list):
        return any(_nested_union(v) for v in node)
    return False


def test_flatten_inlines_refs_and_splices_nested_union():
    original = copy.deepcopy(NESTED)
    out = _flatten_json_schema_unions(NESTED)
    assert NESTED == original, "input must not be mutated"
    assert not _has_key(out, "$ref") and not _has_key(out, "$defs")
    assert not _nested_union(out)
    assert len(out["oneOf"]) == 4  # view + 2 create variants + delete
    assert out["oneOf"][0]["properties"]["id"] == {"type": "string"}
    assert out["oneOf"][3]["properties"]["id"] == {"type": "string"}


def test_flatten_is_idempotent():
    once = _flatten_json_schema_unions(NESTED)
    assert _flatten_json_schema_unions(once) == once


def test_cyclic_ref_is_kept_with_defs():
    schema = {
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/node"}},
        "$defs": {"node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/node"}}}},
    }
    out = _flatten_json_schema_unions(schema)
    assert "$defs" in out  # unresolved (cyclic) refs still resolve upstream


def test_tool_shapes_and_namespace():
    tools = [
        {"type": "function", "name": "a", "parameters": NESTED},
        {"type": "function", "function": {"name": "b", "parameters": NESTED}},
        {"type": "namespace", "name": "ns", "tools": [{"type": "function", "name": "c", "parameters": NESTED}]},
        {"type": "tool_search"},
    ]
    out = flatten_tool_schema_unions(tools)
    assert not _nested_union(out[0]["parameters"])
    assert not _nested_union(out[1]["function"]["parameters"])
    assert not _nested_union(out[2]["tools"][0]["parameters"])
    assert out[3] == {"type": "tool_search"}
    assert tools[0]["parameters"] is NESTED  # original tool dicts untouched


def test_flat_schema_unchanged():
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    assert _flatten_json_schema_unions(schema) == schema


_captured: dict = {}


@pytest.fixture
def capture(monkeypatch):
    def _fake(local_vars):
        _captured["tools"] = local_vars.get("tools")
        raise RuntimeError("stop after hook")

    monkeypatch.setattr(
        ResponsesAPIRequestUtils, "get_requested_response_api_optional_param", staticmethod(_fake)
    )
    _captured.clear()


def _run(**kwargs):
    with pytest.raises(Exception):
        litellm.responses(
            model="openai/global.xai.grok-4.7",
            input="hi",
            api_key="sk-test",
            api_base="https://example.invalid/openai/v1",
            tools=[{"type": "function", "name": "a", "parameters": NESTED}],
            **kwargs,
        )
    assert "tools" in _captured, "hook did not reach the capture point"
    return _captured["tools"]


@pytest.mark.usefixtures("capture")
def test_responses_hook_flattens_when_enabled():
    tools = _run(flatten_tool_schema_unions=True)
    assert not _nested_union(tools[0]["parameters"])


@pytest.mark.usefixtures("capture")
def test_responses_hook_untouched_when_disabled():
    tools = _run()
    assert tools[0]["parameters"] == NESTED
