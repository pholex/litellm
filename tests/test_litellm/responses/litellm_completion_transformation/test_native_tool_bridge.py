import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp.types import Tool

from litellm import Router
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.responses.mcp.litellm_proxy_mcp_handler import LiteLLM_Proxy_MCP_Handler
from litellm.responses.litellm_completion_transformation.custom_tool_bridge import CustomToolStreamRewriter
from litellm.responses.litellm_completion_transformation.native_tool_bridge import ToolBinding, convert_native_tools

SOL = "bedrock/converse/arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.openai.gpt-6.2-sol"
ASTRA = "bedrock/converse/arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.openai.gpt-6.2-astra"
TOOL_TYPES = ["function", "custom", "namespace", "apply_patch", "local_shell", "tool_search", "mcp"]
NAMESPACE = {
    "type": "namespace",
    "name": "notes",
    "tools": [
        {"name": "lookup", "parameters": {"type": "object"}},
        {"type": "custom", "name": "edit"},
    ],
}
TOOL_SEARCH = {"type": "tool_search", "execution": "client"}
MIXED_TOOLS = [
    NAMESPACE,
    {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
    {"type": "custom", "name": "edit"},
    {"type": "apply_patch"},
    {"type": "local_shell"},
    TOOL_SEARCH,
    {"type": "mcp", "server_label": "unregistered", "server_url": "https://example.invalid/protocol"},
]

CALLS = [
    (
        ToolBinding("function", "lookup", "notes"),
        {"name": "x"},
        "function_call",
        {"name": "lookup", "namespace": "notes"},
    ),
    (
        ToolBinding("custom", "edit", "notes"),
        {"input": "PATCH"},
        "custom_tool_call",
        {"name": "edit", "namespace": "notes", "input": "PATCH"},
    ),
    (
        ToolBinding("apply_patch", "apply_patch"),
        {"type": "update_file", "path": "a.txt", "diff": "@@\n-old\n+new"},
        "apply_patch_call",
        {},
    ),
    (
        ToolBinding("local_shell", "local_shell"),
        {"command": ["printf", "OK"]},
        "local_shell_call",
        {"action": {"type": "exec", "command": ["printf", "OK"], "env": {}}},
    ),
    (
        ToolBinding("tool_search", "tool_search"),
        {"query": "notes"},
        "tool_search_call",
        {"execution": "client", "arguments": {"query": "notes"}},
    ),
]


def make_router(model):
    return Router(
        model_list=[
            {
                "model_name": "target",
                "litellm_params": {
                    "model": model,
                    "aws_access_key_id": "synthetic",
                    "aws_secret_access_key": "synthetic",
                    "aws_region_name": "us-east-1",
                    "use_chat_completions_api": True,
                    "bridge_custom_tools_as_functions": True,
                    "bridge_responses_tools_as_functions": True,
                    "allowed_tool_types": TOOL_TYPES,
                },
            }
        ],
        num_retries=0,
    )


@pytest.mark.parametrize("model", [SOL, ASTRA])
@pytest.mark.asyncio
async def test_mixed_native_tools_reach_converse_and_round_trip(model, monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    router = make_router(model)
    requests = []

    async def fake_post(self, url, **kwargs):
        assert url.endswith("/converse")
        requests.append(json.loads(kwargs["data"]))
        content = (
            [
                {"toolUse": {"toolUseId": f"tool{i}", "name": binding.alias, "input": arguments}}
                for i, (binding, arguments, _, _) in enumerate(CALLS)
            ]
            if len(requests) == 1
            else [{"text": "OK"}]
        )
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "output": {"message": {"role": "assistant", "content": content}},
                "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                "stopReason": "tool_use" if len(requests) == 1 else "end_turn",
                "metrics": {"latencyMs": 3},
            },
        )

    message = {"role": "user", "content": "Call the test tools."}
    with patch.object(AsyncHTTPHandler, "post", fake_post):
        result = await router.aresponses(
            model="target",
            input=[message, {"type": "additional_tools", "role": "developer", "tools": MIXED_TOOLS}],
            tool_choice={"type": "function", "name": "lookup", "namespace": "notes"},
        )
        dispatched = requests[0]["toolConfig"]
        assert {item["toolSpec"]["name"] for item in dispatched["tools"]} == {
            "lookup",
            "edit",
            *(binding.alias for binding, _, _, _ in CALLS),
        }
        assert dispatched["toolChoice"]["tool"]["name"] == CALLS[0][0].alias
        returned = [item.model_dump(exclude_none=True) for item in result.output if item.type != "message"]
        for i, (item, (binding, arguments, item_type, expected)) in enumerate(zip(returned, CALLS, strict=True)):
            assert item["type"] == item_type
            assert item["call_id"] == f"tool{i}"
            assert all(item.get(key) == value for key, value in expected.items())
            if binding.kind == "apply_patch":
                assert item["operation"] == arguments

        output_types = [
            "function_call_output",
            "custom_tool_call_output",
            "apply_patch_call_output",
            "local_shell_call_output",
            "tool_search_output",
        ]
        outputs = [
            {"type": output_type, "call_id": f"tool{i}", "output": "RESULT"}
            for i, output_type in enumerate(output_types)
        ]
        outputs[3] = {"type": "local_shell_call_output", "id": "tool3", "output": "RESULT"}
        outputs[4]["tools"] = [{"type": "function", "name": "discovered", "parameters": {"type": "object"}}]
        result = await router.aresponses(model="target", input=[message, *returned, *outputs], tools=MIXED_TOOLS)
        payload = requests[1]
        blocks = [block for item in payload["messages"] for block in item["content"]]
        assert {b["toolUse"]["name"] for b in blocks if "toolUse" in b} == {binding.alias for binding, *_ in CALLS}
        assert {b["toolResult"]["toolUseId"] for b in blocks if "toolResult" in b} == {f"tool{i}" for i in range(5)}
        assert any(b["toolSpec"]["name"] == "discovered" for b in payload["toolConfig"]["tools"])
        assert result.status == "completed"


@pytest.mark.parametrize("binding,arguments,call_type,expected", CALLS)
def test_native_streaming_item_and_completed_payload(binding, arguments, call_type, expected):
    names = {binding.alias} if binding.kind == "custom" else set()
    rewriter = CustomToolStreamRewriter(names, {binding.alias: binding})
    item = {"type": "function_call", "name": binding.alias, "arguments": "", "call_id": "tool1", "id": "tool1"}
    added = rewriter.rewrite({"type": "response.output_item.added", "item": item})
    assert (added is None) == (binding.namespace is None)
    delta = rewriter.rewrite({"type": "response.function_call_arguments.delta", "item_id": "tool1", "delta": "{"})
    assert (delta is None) == (binding.kind != "function")
    item = {**item, "arguments": json.dumps(arguments)}
    completed_item_event = rewriter.rewrite(
        {"type": "response.output_item.done", "item": item, "sequence_number": 5, "output_index": 1}
    )
    if binding.namespace is None:
        assert completed_item_event.type == "response.output_item.added"
        assert completed_item_event.item.status == "in_progress"
        assert completed_item_event.sequence_number == 5
        completed_item_event = rewriter.pop_pending_event()
        assert completed_item_event["sequence_number"] == 6
    assert rewriter.pop_pending_event() is None
    done = completed_item_event["item"]
    assert done.type == call_type
    assert all(getattr(done, key) == value for key, value in expected.items())
    final = rewriter.rewrite({"type": "response.completed", "response": {"output": [item]}, "sequence_number": 6})
    assert final["response"]["output"][0].model_dump() == done.model_dump()
    assert final["sequence_number"] == (7 if binding.namespace is None else 6)


def test_hosted_search_keeps_declared_functions_available_and_namespace_collisions_distinct():
    schemas, _, _, bindings = convert_native_tools(
        [
            {"type": "tool_search", "execution": "server"},
            {"type": "function", "name": "lookup", "defer_loading": True},
            NAMESPACE,
            {**NAMESPACE, "name": "files"},
        ],
        "Hi",
        None,
    )
    assert "lookup" in {tool["name"] for tool in schemas}
    assert len(bindings) == 4
    assert {b.namespace for b in bindings.values()} == {"notes", "files"}


@pytest.mark.asyncio
async def test_registered_mcp_is_resolved_before_native_bridge(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    router = make_router(SOL)
    discovery = AsyncMock(
        return_value=(
            [Tool(name="registered_read", inputSchema={"type": "object"})],
            {"registered_read": "registered"},
        )
    )

    async def fake_post(self, url, **kwargs):
        tools = json.loads(kwargs["data"])["toolConfig"]["tools"]
        assert {entry["toolSpec"]["name"] for entry in tools} == {
            "registered_read",
            ToolBinding("apply_patch", "apply_patch").alias,
        }
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "output": {"message": {"role": "assistant", "content": [{"text": "OK"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                "stopReason": "end_turn",
                "metrics": {"latencyMs": 3},
            },
        )

    tool = {
        "type": "mcp",
        "server_label": "registered",
        "server_url": "litellm_proxy/registered",
        "require_approval": "always",
    }
    with (
        patch.object(LiteLLM_Proxy_MCP_Handler, "_process_mcp_tools_without_openai_transform", discovery),
        patch.object(AsyncHTTPHandler, "post", fake_post),
    ):
        result = await router.aresponses(
            model="target",
            input=[
                {"role": "user", "content": "Reply OK."},
                {"type": "additional_tools", "role": "developer", "tools": [tool, {"type": "apply_patch"}]},
            ],
        )
    discovery.assert_awaited_once()
    assert discovery.await_args.kwargs["mcp_tools_with_litellm_proxy"] == [tool]
    assert result.status == "completed"


def test_stream_response_metadata_accepts_named_tool_choice():
    from types import SimpleNamespace

    from litellm.responses.litellm_completion_transformation.streaming_iterator import (
        LiteLLMCompletionStreamingIterator,
    )

    alias = ToolBinding("apply_patch", "apply_patch").alias
    iterator = LiteLLMCompletionStreamingIterator(
        model=SOL,
        litellm_custom_stream_wrapper=SimpleNamespace(logging_obj=None),
        request_input="Test",
        responses_api_request={"tool_choice": {"type": "function", "name": alias}},
    )
    event = iterator.create_response_created_event()
    assert event.response.model_dump()["tool_choice"] == {"type": "function", "name": alias}


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "function_call", "name": "unregistered"},
        {"type": "function_call", "name": "registered_read", "namespace": "local"},
        {"type": "local_shell_call"},
        {"type": "tool_search_call"},
    ],
)
def test_gateway_leaves_mixed_client_and_mcp_batches_for_the_caller(extra):
    from types import SimpleNamespace

    known_call = {"type": "function_call", "name": "registered_read"}
    mapping = {"registered_read": "registered"}
    assert LiteLLM_Proxy_MCP_Handler.is_mcp_only_response(SimpleNamespace(output=[known_call]), mapping)
    assert not LiteLLM_Proxy_MCP_Handler.is_mcp_only_response(SimpleNamespace(output=[known_call, extra]), mapping)
