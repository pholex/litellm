"""Adapt client-executed Responses tools to function-only providers."""

import hashlib
import json
from dataclasses import dataclass

from openai.types.responses import ResponseApplyPatchToolCall
from openai.types.responses.response_input_item import LocalShellCall


@dataclass(frozen=True)
class ToolBinding:
    kind: str
    name: str
    namespace: str | None = None

    @property
    def alias(self) -> str:
        key = json.dumps((self.kind, self.name, self.namespace)).encode()
        return f"litellm_{self.kind}_{hashlib.sha256(key).hexdigest()[:32]}"


_BINDINGS = {name: ToolBinding(name, name) for name in ("apply_patch", "local_shell", "tool_search")}

_NATIVE_SCHEMA = {
    "apply_patch": (
        "Ask the client to apply a single file operation. Use a workspace-relative path.",
        {
            "type": {"type": "string", "enum": ["create_file", "update_file", "delete_file"]},
            "path": {"type": "string"},
            "diff": {"type": "string", "description": "Unified diff for create_file or update_file."},
        },
        ["type", "path"],
    ),
    "local_shell": (
        "Ask the client to execute a command as a list of arguments.",
        {
            "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "working_directory": {"type": "string"},
            "timeout_ms": {"type": "integer"},
            "user": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        ["command"],
    ),
    "tool_search": (
        "Ask the client to look up additional tools.",
        {"query": {"type": "string"}},
        ["query"],
    ),
}


def _native_tool(tool: dict, binding: ToolBinding) -> dict:
    description, properties, required = _NATIVE_SCHEMA[binding.kind]
    return {
        "type": "function",
        "name": binding.alias,
        "description": tool.get("description") or description,
        "parameters": tool.get("parameters")
        or {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _namespace_call_binding(item: dict) -> ToolBinding | None:
    namespace, name, kind = item.get("namespace"), item.get("name"), item.get("type")
    if not isinstance(namespace, str) or not isinstance(name, str) or not namespace or not name:
        return None
    if kind in ("function_call", "custom_tool_call"):
        return ToolBinding("custom" if kind == "custom_tool_call" else "function", name, namespace)
    return None


def _convert_history(item: dict) -> dict:
    kind = item.get("type")
    binding = _namespace_call_binding(item)
    if binding:
        return {**item, "name": binding.alias}
    for tool_kind, field in (("apply_patch", "operation"), ("local_shell", "action"), ("tool_search", "arguments")):
        if kind == f"{tool_kind}_call":
            args = item.get(field, {})
            if tool_kind == "local_shell" and isinstance(args, dict):
                args = {key: value for key, value in args.items() if key != "type"}
            return {
                "type": "function_call",
                "call_id": item.get("call_id") or item.get("id"),
                "name": _BINDINGS[tool_kind].alias,
                "arguments": json.dumps(args),
            }
        if kind == f"{tool_kind}_call_output" or (tool_kind == "tool_search" and kind == "tool_search_output"):
            # Native local-shell results identify the call via id.
            call_id = item.get("call_id") or (item.get("id") if tool_kind == "local_shell" else None)
            if not call_id and tool_kind == "tool_search":
                return {"type": "message", "role": "developer", "content": "Tool search results are available."}
            result = (
                "Tool search results are available."
                if tool_kind == "tool_search"
                else item.get("output") or item.get("status") or ""
            )
            return {"type": "function_call_output", "call_id": call_id, "output": result}
    return item


def convert_native_tools(
    tools: list[dict], request_input: str | list[dict], tool_choice: str | dict | None
) -> tuple[list[dict], str | list[dict], str | dict | None, dict[str, ToolBinding]]:
    loaded_tools = [
        tool
        for item in (request_input if isinstance(request_input, list) else ())
        if isinstance(item, dict) and item.get("type") == "tool_search_output"
        for tool in item.get("tools") or ()
    ]
    converted: dict[str, dict] = {}
    bindings: dict[str, ToolBinding] = {}
    reserved = {
        tool["name"]
        for tool in (*tools, *loaded_tools)
        if isinstance(tool, dict) and tool.get("type") in (None, "function", "custom") and tool.get("name")
    }

    def add_binding(binding: ToolBinding, schema: dict) -> None:
        if binding.alias in reserved:
            raise ValueError(f"Tool name conflicts with the Responses bridge: {binding.alias}")
        bindings[binding.alias] = binding
        converted[binding.alias] = schema

    for tool in (*tools, *loaded_tools):
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type") or "function"
        if kind in ("function", "custom") and tool.get("name"):
            converted[tool["name"]] = {**tool, "type": kind}
        elif kind == "namespace" and tool.get("name"):
            for nested in tool.get("tools") or ():
                if not isinstance(nested, dict) or not nested.get("name"):
                    continue
                nested_kind = nested.get("type") or "function"
                if nested_kind in ("function", "custom"):
                    binding = ToolBinding(nested_kind, nested["name"], tool["name"])
                    description = " / ".join(
                        text
                        for text in (tool["name"], tool.get("description"), nested["name"], nested.get("description"))
                        if text
                    )
                    add_binding(
                        binding, {**nested, "type": nested_kind, "name": binding.alias, "description": description}
                    )
        elif (
            isinstance(kind, str) and kind in _BINDINGS and (kind != "tool_search" or tool.get("execution") == "client")
        ):
            binding = _BINDINGS[kind]
            add_binding(binding, _native_tool(tool, binding))

    if isinstance(tool_choice, dict):
        kind = tool_choice.get("type")
        targeted = _BINDINGS.get(kind) if isinstance(kind, str) else None
        if kind in ("function", "custom") and tool_choice.get("namespace") and tool_choice.get("name"):
            targeted = ToolBinding(kind, tool_choice["name"], tool_choice["namespace"])
        if targeted and targeted.alias in bindings:
            tool_choice = {"type": "function", "name": targeted.alias}
        elif targeted:
            tool_choice = None

    converted_input = (
        [_convert_history(item) if isinstance(item, dict) else item for item in request_input]
        if isinstance(request_input, list)
        else request_input
    )
    return list(converted.values()), converted_input, tool_choice, bindings


def restore_native_tool_call(item: dict, binding: ToolBinding) -> dict:
    if binding.namespace is not None:
        return {**item, "name": binding.name, "namespace": binding.namespace}
    try:
        arguments = json.loads(item.get("arguments") or "")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        base = {
            "id": item.get("id") or item.get("call_id"),
            "call_id": item.get("call_id") or item.get("id"),
            "status": "completed",
        }
        if binding.kind == "apply_patch":
            call = ResponseApplyPatchToolCall.model_validate(
                {**base, "type": "apply_patch_call", "operation": arguments}
            )
        elif binding.kind == "local_shell":
            call = LocalShellCall.model_validate(
                {**base, "type": "local_shell_call", "action": {"env": {}, **arguments, "type": "exec"}}
            )
        else:
            if not base["id"]:
                raise ValueError("Tool search requires a call identifier")
            return {**base, "type": "tool_search_call", "execution": "client", "arguments": arguments}
        return call.model_dump(exclude_none=True)
    except ValueError:
        raise ValueError(f"Invalid arguments for the bridged {binding.kind} tool call") from None
