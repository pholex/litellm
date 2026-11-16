from copy import deepcopy
from typing import AsyncIterator, Iterator, Optional, cast

import httpx
from tiktoken import Encoding

from pydantic import JsonValue, TypeAdapter

from litellm.llms.bedrock.chat.invoke_transformations.amazon_openai_transformation import (
    AmazonBedrockOpenAIConfig,
)
from litellm.llms.bedrock.common_utils import strip_bedrock_routing_prefix
from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig, OpenAIChatCompletionStreamingHandler
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.types.utils import ModelResponse, ModelResponseStream, Usage
from litellm.types.llms.openai import AllMessageValues


class BedrockRuntimeChatConfig(AmazonBedrockOpenAIConfig):
    """Opt-in Runtime Chat Completions using AWS credentials, never bearer keys."""

    def get_supported_openai_params(self, model: str) -> list[str]:
        return [
            "max_tokens",
            "max_completion_tokens",
            "stream",
            "stream_options",
            "temperature",
            "top_p",
            "stop",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "response_format",
            "reasoning_effort",
            "service_tier",
        ]

    def get_complete_url(
        self,
        api_base: Optional[str],
        api_key: Optional[str],
        model: str,
        optional_params: dict[str, JsonValue],
        litellm_params: dict[str, JsonValue],
        stream: Optional[bool] = None,
    ) -> str:
        endpoint, _ = self.get_runtime_endpoint(
            api_base=api_base,
            aws_bedrock_runtime_endpoint=TypeAdapter[Optional[str]](Optional[str]).validate_python(
                optional_params.get("aws_bedrock_runtime_endpoint")
            ),
            aws_region_name=self._get_aws_region_name(optional_params, model),  # pyright: ignore[reportUnknownMemberType]
        )
        return f"{endpoint.rstrip('/')}/openai/v1/chat/completions"

    def _should_preserve_cache_control_for_endpoint(
        self, custom_llm_provider: Optional[str], api_base: Optional[str]
    ) -> bool:
        return True

    def _runtime_optional_params(self, optional_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        params = {k: v for k, v in optional_params.items() if k not in self.aws_authentication_params}
        reasoning = params.get("reasoning_effort")
        if not isinstance(reasoning, dict):
            return params
        # The Responses bridge retains summary in a dict; Runtime accepts only effort.
        return {
            **{k: v for k, v in params.items() if k != "reasoning_effort"},
            **({"reasoning_effort": reasoning["effort"]} if reasoning.get("effort") is not None else {}),
        }

    def transform_request(
        self,
        model: str,
        messages: list[AllMessageValues],
        optional_params: dict[str, JsonValue],
        litellm_params: dict[str, JsonValue],
        headers: dict[str, str],
    ) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            OpenAIGPTConfig.transform_request(  # pyright: ignore[reportUnknownMemberType]
                self,
                model=strip_bedrock_routing_prefix(model),
                messages=deepcopy(messages),
                optional_params=self._runtime_optional_params(optional_params),
                litellm_params=litellm_params,
                headers=headers,
            ),
        )

    async def async_transform_request(
        self,
        model: str,
        messages: list[AllMessageValues],
        optional_params: dict[str, JsonValue],
        litellm_params: dict[str, JsonValue],
        headers: dict[str, str],
    ) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            await OpenAIGPTConfig.async_transform_request(  # pyright: ignore[reportUnknownMemberType]
                self,
                model=strip_bedrock_routing_prefix(model),
                messages=deepcopy(messages),
                optional_params=self._runtime_optional_params(optional_params),
                litellm_params=litellm_params,
                headers=headers,
            ),
        )

    def sign_request(
        self,
        headers: dict[str, str],
        optional_params: dict[str, JsonValue],
        request_data: dict[str, JsonValue],
        api_base: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        stream: Optional[bool] = None,
        fake_stream: Optional[bool] = None,
    ) -> tuple[dict[str, str], Optional[bytes]]:
        # Empty api_key bypasses AWS_BEARER_TOKEN_BEDROCK in the shared signer.
        # Strip forwarded authorization so it cannot overwrite the SigV4 result.
        return cast(
            tuple[dict[str, str], Optional[bytes]],
            self._sign_request(  # pyright: ignore[reportUnknownMemberType]
                service_name="bedrock",
                headers={k: v for k, v in headers.items() if k.lower() != "authorization"},
                optional_params=optional_params,
                request_data={**request_data, "stream": True} if stream else request_data,
                api_base=api_base,
                api_key="",
                model=model,
                stream=stream,
                fake_stream=fake_stream,
            ),
        )

    def transform_response(
        self,
        model: str,
        raw_response: httpx.Response,
        model_response: ModelResponse,
        logging_obj: Logging,
        request_data: dict[str, JsonValue],
        messages: list[AllMessageValues],
        optional_params: dict[str, JsonValue],
        litellm_params: dict[str, JsonValue],
        encoding: Encoding,
        api_key: Optional[str] = None,
        json_mode: Optional[bool] = None,
    ) -> ModelResponse:
        response = super().transform_response(  # pyright: ignore[reportUnknownMemberType]
            model=model,
            raw_response=raw_response,
            model_response=model_response,
            logging_obj=logging_obj,
            request_data=request_data,
            messages=messages,
            optional_params=optional_params,
            litellm_params=litellm_params,
            encoding=encoding,
            api_key=api_key,
            json_mode=json_mode,
        )
        return response.model_copy(update={"usage": _native_usage(getattr(response, "usage", None))})

    def get_model_response_iterator(
        self,
        streaming_response: Iterator[str] | AsyncIterator[str] | ModelResponse,
        sync_stream: bool,
        json_mode: Optional[bool] = False,
    ) -> "BedrockRuntimeChatStream":
        return BedrockRuntimeChatStream(streaming_response, sync_stream, json_mode)


def _native_usage(usage: object) -> Optional[Usage]:
    if not isinstance(usage, Usage):
        return None
    details = usage.prompt_tokens_details
    if details is None or getattr(details, "cache_creation_tokens", None) is not None:
        return usage
    write_tokens = getattr(details, "cache_write_tokens", None)
    if not isinstance(write_tokens, int) or write_tokens < 0:
        return usage
    # Runtime's OpenAI usage includes cache writes in prompt_tokens; the common
    # Bedrock cost calculator reads the same count as cache_creation_tokens.
    return usage.model_copy(
        update={"prompt_tokens_details": details.model_copy(update={"cache_creation_tokens": write_tokens})}
    )


class BedrockRuntimeChatStream(OpenAIChatCompletionStreamingHandler):
    def chunk_parser(self, chunk: dict[str, JsonValue]) -> ModelResponseStream:
        response = super().chunk_parser(chunk)  # pyright: ignore[reportUnknownMemberType]
        usage = _native_usage(getattr(response, "usage", None))
        return response.model_copy(update={"usage": usage}) if usage is not None else response
