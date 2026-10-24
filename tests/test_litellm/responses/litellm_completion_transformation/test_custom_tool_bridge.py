import json

from litellm.responses.litellm_completion_transformation.custom_tool_bridge import (
    CustomToolStreamRewriter,
    convert_custom_tools_request,
    drop_empty_assistant_messages,
    rewrite_response_object,
)
from litellm.types.llms.base import BaseLiteLLMOpenAIResponseObject

APPLY_PATCH = {
    "type": "custom",
    "name": "apply_patch",
    "description": "Edit files.",
    "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"},
}


def test_custom_tool_and_history_become_function_items():
    tools, new_input, names = convert_custom_tools_request(
        tools=[APPLY_PATCH, {"type": "function", "name": "shell", "parameters": {}}],
        input=[
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "custom_tool_call", "call_id": "c1", "name": "apply_patch", "input": "*** Begin Patch"},
            {"type": "custom_tool_call_output", "call_id": "c1", "output": "ok"},
        ],
    )
    assert names == {"apply_patch"}
    fn = tools[0]
    assert fn["type"] == "function" and fn["name"] == "apply_patch"
    assert fn["parameters"]["required"] == ["input"]
    assert "start: /.+/" in fn["description"]
    assert tools[1]["name"] == "shell"
    assert new_input[1] == {
        "type": "function_call",
        "call_id": "c1",
        "name": "apply_patch",
        "arguments": json.dumps({"input": "*** Begin Patch"}),
    }
    assert new_input[2] == {"type": "function_call_output", "call_id": "c1", "output": "ok"}


def test_function_call_output_is_rewritten_to_custom_tool_call():
    class Resp:
        output = [
            BaseLiteLLMOpenAIResponseObject(type="message", role="assistant", content=[]),
            BaseLiteLLMOpenAIResponseObject(
                type="function_call", id="c1", call_id="c1", name="apply_patch", arguments='{"input": "PATCH"}'
            ),
            BaseLiteLLMOpenAIResponseObject(type="function_call", id="c2", call_id="c2", name="shell", arguments="{}"),
        ]

    resp = rewrite_response_object(Resp(), {"apply_patch"})
    assert resp.output[1].type == "custom_tool_call"
    assert resp.output[1].input == "PATCH"
    assert resp.output[1].call_id == "c1"
    assert resp.output[2].type == "function_call"


def test_stream_rewriter_swallows_arg_deltas_and_rewrites_items():
    rw = CustomToolStreamRewriter({"apply_patch"})
    added = BaseLiteLLMOpenAIResponseObject(
        type="response.output_item.added",
        item=BaseLiteLLMOpenAIResponseObject(type="function_call", id="c1", call_id="c1", name="apply_patch", arguments=""),
    )
    out = rw.rewrite(added)
    assert out.item.type == "custom_tool_call" and out.item.input == ""
    delta = BaseLiteLLMOpenAIResponseObject(type="response.function_call_arguments.delta", item_id="c1", delta='{"in')
    assert rw.rewrite(delta) is None
    other = BaseLiteLLMOpenAIResponseObject(type="response.function_call_arguments.delta", item_id="c9", delta="x")
    assert rw.rewrite(other) is other
    done = BaseLiteLLMOpenAIResponseObject(
        type="response.output_item.done",
        item=BaseLiteLLMOpenAIResponseObject(
            type="function_call", id="c1", call_id="c1", name="apply_patch", arguments='{"input": "P"}'
        ),
    )
    assert rw.rewrite(done).item.input == "P"


def test_drop_empty_assistant_messages():
    items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "", "annotations": []}]},
        {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{}"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
    ]
    out = drop_empty_assistant_messages(items)
    assert [i.get("type") for i in out] == ["message", "function_call", "message"]
    assert out[-1]["content"][0]["text"] == "done"
    assert drop_empty_assistant_messages("plain string") == "plain string"


def test_stream_rewriter_handles_enum_event_types():
    from litellm.types.llms.openai import ResponsesAPIStreamEvents

    rw = CustomToolStreamRewriter({"apply_patch"})
    added = BaseLiteLLMOpenAIResponseObject(
        type=ResponsesAPIStreamEvents.OUTPUT_ITEM_ADDED,
        item=BaseLiteLLMOpenAIResponseObject(type="function_call", id="c1", call_id="c1", name="apply_patch", arguments=""),
    )
    assert rw.rewrite(added).item.type == "custom_tool_call"
    delta = BaseLiteLLMOpenAIResponseObject(
        type=ResponsesAPIStreamEvents.FUNCTION_CALL_ARGUMENTS_DELTA, item_id="c1", delta="x"
    )
    assert rw.rewrite(delta) is None
