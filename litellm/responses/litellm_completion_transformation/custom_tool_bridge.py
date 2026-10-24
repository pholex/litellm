"""
Responses -> chat-completions bridge helpers for Codex-style ``custom`` tools.

Codex declares ``apply_patch`` (and other freeform tools) as Responses API
``{"type": "custom", "name": ..., "format": {"type": "grammar", ...}}`` tools.
Chat-completions upstreams reached through the bridge (Gemini via ``gemini/``,
Moonshot/Kimi via ``use_chat_completions_api``) only understand ``function``
tools, so these were dropped (or filtered by ``drop_namespace_tools``) and the
model never saw ``apply_patch`` -- it fell back to editing files through the
shell tool.

Enabled per deployment with ``litellm_params.bridge_custom_tools_as_functions:
true``. When on:

* request: each custom tool becomes a function tool with a single string
  parameter ``input`` (grammar copied into the description); history items
  ``custom_tool_call`` / ``custom_tool_call_output`` become ``function_call`` /
  ``function_call_output`` with ``arguments = {"input": ...}``;
* response: ``function_call`` items whose name is one of those custom tools are
  turned back into ``custom_tool_call`` items with the raw ``input`` string, in
  both the non-streaming response and the streaming events, so the client sees
  exactly what a native Responses API provider would return.

Also home of :func:`drop_empty_assistant_messages`, applied to every bridged
request (see its docstring).
"""

import json
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

INPUT_PARAM = "input"
_PRIVATE_NAMES_KEY = "_bridge_custom_tool_names"


def _custom_tool_to_function(tool: Dict[str, Any]) -> Dict[str, Any]:
    description = str(tool.get("description") or "")
    fmt = tool.get("format") if isinstance(tool.get("format"), dict) else {}
    if fmt.get("type") == "grammar" and fmt.get("definition"):
        description = (
            f"{description}\n\n"
            f"Pass the raw tool input as the `{INPUT_PARAM}` string (not JSON-encoded further). "
            f"It must conform to this {fmt.get('syntax') or 'grammar'} grammar:\n{fmt.get('definition')}"
        ).strip()
    else:
        description = f"{description}\n\nPass the raw tool input as the `{INPUT_PARAM}` string.".strip()
    return {
        "type": "function",
        "name": tool.get("name") or "",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {INPUT_PARAM: {"type": "string", "description": "Raw freeform tool input."}},
            "required": [INPUT_PARAM],
        },
        "strict": False,
    }


def convert_custom_tools_request(
    tools: Optional[Iterable[Any]], input: Any
) -> Tuple[Optional[List[Any]], Any, Set[str]]:
    """Rewrite custom tools to function tools and custom history items to function items.

    Returns ``(tools, input, custom_tool_names)``; inputs are copied, never mutated.
    """
    names: Set[str] = set()
    new_tools: Optional[List[Any]] = None
    if tools is not None:
        new_tools = []
        for tool in tools:
            if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name"):
                names.add(str(tool["name"]))
                new_tools.append(_custom_tool_to_function(tool))
            else:
                new_tools.append(tool)

    if isinstance(input, list):
        converted: List[Any] = []
        changed = False
        for item in input:
            if isinstance(item, dict) and item.get("type") == "custom_tool_call":
                names.add(str(item.get("name") or ""))
                new_item = {
                    "type": "function_call",
                    "call_id": item.get("call_id") or item.get("id") or "",
                    "name": item.get("name") or "",
                    "arguments": json.dumps({INPUT_PARAM: item.get("input") or ""}, ensure_ascii=False),
                }
                if item.get("id"):
                    new_item["id"] = item["id"]
                converted.append(new_item)
                changed = True
            elif isinstance(item, dict) and item.get("type") == "custom_tool_call_output":
                converted.append(
                    {"type": "function_call_output", "call_id": item.get("call_id") or "", "output": item.get("output")}
                )
                changed = True
            else:
                converted.append(item)
        if changed:
            input = converted
    names.discard("")
    return new_tools, input, names


def drop_empty_assistant_messages(input: Any) -> Any:
    """Drop replayed assistant message items whose text is entirely empty.

    The bridge emits an assistant ``message`` item with ``output_text: ""``
    when the model answers with tool calls only. Clients replay it verbatim on
    the next turn, and strict upstreams reject an empty assistant text
    (Moonshot/Kimi: 400 "Invalid request: text content is empty"), so the
    second turn of every tool loop failed. An empty assistant message carries
    no information, so removing it is safe for every provider.
    """
    if not isinstance(input, list):
        return input
    kept: List[Any] = []
    changed = False
    for item in input:
        if (
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and item.get("type", "message") == "message"
        ):
            content = item.get("content")
            if content is None or content == "":
                changed = True
                continue
            if isinstance(content, list) and all(
                isinstance(p, dict)
                and p.get("type") in ("output_text", "text", "input_text")
                and not (p.get("text") or "").strip()
                for p in content
            ):
                changed = True
                continue
        kept.append(item)
    return kept if changed else input


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _type_str(value: Any) -> str:
    """Event/item ``type`` as a plain string (events carry str-Enum members whose str() is the member name)."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _raw_input_from_arguments(arguments: Any) -> str:
    if not arguments:
        return ""
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return str(arguments)
    if isinstance(parsed, dict) and isinstance(parsed.get(INPUT_PARAM), str):
        return parsed[INPUT_PARAM]
    return str(arguments)


def to_custom_tool_call_item(item: Any, names: Set[str]) -> Optional[Dict[str, Any]]:
    """Return a ``custom_tool_call`` dict if ``item`` is a function_call to a custom tool."""
    if _type_str(_get(item, "type")) != "function_call" or _get(item, "name") not in names:
        return None
    out: Dict[str, Any] = {
        "type": "custom_tool_call",
        "call_id": _get(item, "call_id") or _get(item, "id") or "",
        "name": _get(item, "name"),
        "input": _raw_input_from_arguments(_get(item, "arguments")),
    }
    if _get(item, "id"):
        out["id"] = _get(item, "id")
    if _get(item, "status"):
        out["status"] = _get(item, "status")
    provider_specific_fields = _get(item, "provider_specific_fields")
    if provider_specific_fields:
        out["provider_specific_fields"] = provider_specific_fields
    return out


def rewrite_output_items(output: Any, names: Set[str]) -> Any:
    if not names or not isinstance(output, list):
        return output
    from litellm.types.llms.base import BaseLiteLLMOpenAIResponseObject

    new_output = []
    changed = False
    for item in output:
        custom = to_custom_tool_call_item(item, names)
        if custom is not None:
            new_output.append(BaseLiteLLMOpenAIResponseObject(**custom))
            changed = True
        else:
            new_output.append(item)
    return new_output if changed else output


def rewrite_response_object(response: Any, names: Set[str]) -> Any:
    """In-place rewrite of a ResponsesAPIResponse (or similar) ``output`` list."""
    if not names or response is None:
        return response
    output = _get(response, "output")
    new_output = rewrite_output_items(output, names)
    if new_output is not output:
        try:
            setattr(response, "output", new_output)
        except Exception:
            if isinstance(response, dict):
                response["output"] = new_output
    return response


class CustomToolStreamRewriter:
    """Stateful rewriter for bridged Responses streaming events.

    ``rewrite(event)`` returns the (possibly rewritten) event, or ``None`` when
    the event must be swallowed (argument deltas of a custom tool call).
    """

    def __init__(self, names: Set[str]):
        self.names = names
        self._custom_item_ids: Set[str] = set()

    def rewrite(self, event: Any) -> Any:
        if not self.names:
            return event
        etype = _type_str(_get(event, "type"))
        if etype.endswith("output_item.added") or etype.endswith("output_item.done"):
            item = _get(event, "item")
            custom = to_custom_tool_call_item(item, self.names)
            if custom is not None:
                from litellm.types.llms.base import BaseLiteLLMOpenAIResponseObject

                item_id = str(_get(item, "id") or _get(item, "call_id") or "")
                if item_id:
                    self._custom_item_ids.add(item_id)
                new_item = BaseLiteLLMOpenAIResponseObject(**custom)
                if isinstance(event, dict):
                    event = dict(event)
                    event["item"] = new_item
                else:
                    setattr(event, "item", new_item)
            return event
        if etype.endswith("function_call_arguments.delta") or etype.endswith("function_call_arguments.done"):
            if str(_get(event, "item_id") or "") in self._custom_item_ids:
                return None
            return event
        response = _get(event, "response")
        if response is not None and etype.startswith("response."):
            rewrite_response_object(response, self.names)
        return event


def pop_custom_tool_names(kwargs: Dict[str, Any]) -> Set[str]:
    names = kwargs.pop(_PRIVATE_NAMES_KEY, None)
    return set(names) if names else set()


def stash_custom_tool_names(kwargs: Dict[str, Any], names: Set[str]) -> None:
    if names:
        kwargs[_PRIVATE_NAMES_KEY] = sorted(names)
