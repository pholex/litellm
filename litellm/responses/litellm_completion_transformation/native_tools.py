import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, Literal, cast

from openai.types.responses.response_apply_patch_tool_call import ResponseApplyPatchToolCall
from openai.types.responses.response_output_item import LocalShellCall
from pydantic import ValidationError

from litellm.types.llms.openai import ResponseInputParam

NativeTool = Literal["apply_patch", "local_shell"]
NativeToolCall = ResponseApplyPatchToolCall | LocalShellCall
_NATIVE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"apply_patch": "litellm_native_apply_patch", "local_shell": "litellm_native_local_shell"}
)
_NATIVE_SCHEMAS: Final[Mapping[str, Mapping[str, object]]] = MappingProxyType(
    {
        "apply_patch": {
            "type": "object",
            "required": ["type", "path"],
            "properties": {
                "type": {"type": "string", "enum": ["create_file", "update_file", "delete_file"]},
                "path": {"type": "string", "description": "Workspace-relative path."},
                "diff": {"type": "string"},
            },
        },
        "local_shell": {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "working_directory": {"type": "string"},
                "timeout_ms": {"type": "integer"},
                "user": {"type": "string"},
            },
        },
    }
)


def native_tool_bridge_enabled(request: Mapping[str, object]) -> bool:
    return request.get("_bridge_native_tools") is True


def _tool_kind(tool: object) -> NativeTool | None:
    kind = tool.get("type") if isinstance(tool, Mapping) else None
    if kind == "apply_patch":
        return "apply_patch"
    if kind == "local_shell":
        return "local_shell"
    return None


def native_tool_kind(alias: str, response_tools: Sequence[object] | None) -> NativeTool | None:
    kind = next(
        (
            name
            for name in (_tool_kind(tool) for tool in response_tools or ())
            if name is not None and _NATIVE_ALIASES[name] == alias
        ),
        None,
    )
    return kind


def _native_tool_definition(tool: Mapping[str, object]) -> Mapping[str, object]:
    kind = _tool_kind(tool)
    if kind is None:
        return tool
    description = (
        "Ask the client to apply a single workspace file operation."
        if kind == "apply_patch"
        else "Ask the client to run a command given as an argument array."
    )
    return {
        "type": "function",
        "name": _NATIVE_ALIASES[kind],
        "description": tool.get("description") or description,
        "parameters": tool.get("parameters") or _NATIVE_SCHEMAS[kind],
    }


def native_tool_definitions(tools: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    aliases = frozenset(_NATIVE_ALIASES[kind] for tool in tools if (kind := _tool_kind(tool)) is not None)
    if any(tool.get("name") in aliases for tool in tools if isinstance(tool.get("name"), str)):
        raise ValueError("Tool name conflicts with a native Responses tool alias")
    return tuple(_native_tool_definition(tool) for tool in tools)


def native_tool_choice(choice: object, tools: Sequence[object]) -> object:
    kind = _tool_kind(choice)
    if kind is None:
        return choice
    return (
        {"type": "function", "name": _NATIVE_ALIASES[kind]} if any(_tool_kind(tool) == kind for tool in tools) else None
    )


def _convert_native_history(item: object) -> object:
    if not isinstance(item, Mapping):
        return item
    match item.get("type"):
        case "apply_patch_call" | "local_shell_call" as kind:
            native_kind = kind.removesuffix("_call")
            raw_args = item.get("operation") if native_kind == "apply_patch" else item.get("action")
            args = (
                {key: value for key, value in raw_args.items() if key != "type"}
                if native_kind == "local_shell" and isinstance(raw_args, Mapping)
                else raw_args
            )
            return {
                "type": "function_call",
                "call_id": item.get("call_id") or item.get("id"),
                "name": _NATIVE_ALIASES[native_kind],
                "arguments": json.dumps(args or {}),
            }
        case "apply_patch_call_output" | "local_shell_call_output":
            return {
                "type": "function_call_output",
                "call_id": item.get("call_id") or item.get("id"),
                "output": item.get("output") or item.get("status") or "",
            }
        case _:
            return item


def native_tool_history(items: str | ResponseInputParam) -> str | ResponseInputParam:
    if isinstance(items, str):
        return items
    shell_ids = {
        item.get("id"): item.get("call_id")
        for item in items
        if isinstance(item, Mapping)
        and item.get("type") == "local_shell_call"
        and isinstance(item.get("id"), str)
        and isinstance(item.get("call_id"), str)
    }
    converted = [
        _convert_native_history(
            {**item, "call_id": shell_ids.get(item.get("id"), item.get("id"))}
            if isinstance(item, Mapping)
            and item.get("type") == "local_shell_call_output"
            and isinstance(item.get("id"), str)
            else item
        )
        for item in items
    ]
    return cast(ResponseInputParam, converted)


def native_tool_output(kind: NativeTool, call_id: str, arguments: str) -> NativeToolCall | None:
    try:
        params = json.loads(arguments)
        if not isinstance(params, dict):
            return None
        if kind == "apply_patch":
            return ResponseApplyPatchToolCall.model_validate(
                {
                    "id": call_id,
                    "call_id": call_id,
                    "type": "apply_patch_call",
                    "status": "completed",
                    "operation": params,
                }
            )
        return LocalShellCall.model_validate(
            {
                "id": call_id,
                "call_id": call_id,
                "type": "local_shell_call",
                "status": "completed",
                "action": {"env": {}, **params, "type": "exec"},
            }
        )
    except (json.JSONDecodeError, ValidationError):
        return None
