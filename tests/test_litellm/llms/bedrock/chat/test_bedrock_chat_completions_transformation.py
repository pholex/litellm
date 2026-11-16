import json
from copy import deepcopy

import httpx
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

import litellm
from litellm.llms.bedrock.chat.chat_completions_transformation import BedrockRuntimeChatConfig
from litellm.llms.bedrock.common_utils import BedrockModelInfo, get_bedrock_chat_config
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler

MODEL = "bedrock/chat_completions/us.moonshotai.kimi-k3.1"
UPSTREAM = "us.moonshotai.kimi-k3.1"
URL = "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/chat/completions"


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch, tmp_path):
    for name in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_BEDROCK_RUNTIME_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fake-session")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "must-not-use-bearer")
    BedrockRuntimeChatConfig().iam_cache.flush_cache()
    yield
    BedrockRuntimeChatConfig().iam_cache.flush_cache()


@pytest.mark.parametrize(
    "model,route",
    [
        (MODEL, "chat_completions"),
        ("chat_completions/global.moonshotai.kimi-k3.1", "chat_completions"),
        ("bedrock/converse/us.openai.gpt-5.5", "converse"),
        ("bedrock/openai/arn:aws:bedrock:us-east-1:123:imported-model/test", "openai"),
        ("bedrock/mantle/anthropic.claude-mythos-preview", "mantle"),
    ],
)
def test_route_is_explicit(model, route):
    assert BedrockModelInfo.get_bedrock_route(model) == route


def test_native_route_config_and_base_model():
    assert isinstance(get_bedrock_chat_config(MODEL), BedrockRuntimeChatConfig)
    assert BedrockModelInfo.get_base_model(MODEL) == "moonshotai.kimi-k3.1"
    assert BedrockModelInfo.get_bedrock_route("moonshotai.kimi-k3.1") != "chat_completions"


@pytest.mark.parametrize("stream", [False, True])
def test_transform_preserves_tool_history_reasoning_vision_and_cache(stream):
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                {"type": "text", "text": "Inspect this", "cache_control": {"type": "ephemeral"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "Prior reasoning",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "42"},
    ]
    params = {
        "stream": stream,
        "max_tokens": 128,
        "reasoning_effort": "low",
        "aws_region_name": "us-east-1",
        "aws_session_token": "private",
        "aws_role_name": "arn:aws:iam::123:role/test",
        "stream_options": {"include_usage": True},
    }
    original = deepcopy((messages, params))
    body = BedrockRuntimeChatConfig().transform_request(MODEL, messages, params, {}, {})
    assert body["model"] == UPSTREAM
    assert body["messages"] == original[0]
    assert body["stream"] == stream
    assert body["reasoning_effort"] == "low"
    assert body["stream_options"] == {"include_usage": True}
    assert not any(k.startswith("aws_") for k in body)
    assert (messages, params) == original


def assert_signed_request(request):
    assert str(request.url) == URL
    assert request.headers["x-amz-security-token"] == "fake-session"
    authorization = request.headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=fake-access/")
    assert "/us-east-1/bedrock/aws4_request" in authorization
    signed_names = authorization.split("SignedHeaders=", 1)[1].split(",", 1)[0].split(";")
    unsigned = AWSRequest(
        method="POST",
        url=str(request.url),
        data=request.content,
        headers={k: request.headers[k] for k in signed_names if k != "host"},
    )
    unsigned.context["timestamp"] = request.headers["x-amz-date"]
    signer = SigV4Auth(Credentials("fake-access", "fake-secret", "fake-session"), "bedrock", "us-east-1")
    canonical = signer.canonical_request(unsigned)
    expected = signer.signature(signer.string_to_sign(unsigned, canonical), unsigned)
    assert authorization.rsplit("Signature=", 1)[1] == expected
    body = json.loads(request.content)
    assert body["model"] == UPSTREAM
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 128
    assert not any(k.startswith("aws_") for k in body)
    assert "fake-secret" not in request.content.decode()
    return body


def provider_response(request):
    body = assert_signed_request(request)
    if body.get("stream"):
        chunks = [
            {"delta": {"role": "assistant", "reasoning_content": "Think"}, "finish_reason": None},
            {"delta": {"content": "OK"}, "finish_reason": None},
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ]
                },
                "finish_reason": None,
            },
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        events = [
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": UPSTREAM,
                "choices": [{"index": 0, **c}],
            }
            for c in chunks
        ]
        usage = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": UPSTREAM,
            "choices": [],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 1},
            },
        }
        payload = "".join(f"data: {json.dumps(c)}\n\n" for c in [*events, usage]) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": UPSTREAM,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "OK",
                        "reasoning_content": "Think",
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 1},
            },
        },
    )


def completion_args(stream):
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Hi"}],
        "aws_region_name": "us-east-1",
        "reasoning_effort": "low",
        "max_tokens": 128,
        "stream": stream,
        "stream_options": {"include_usage": True},
        "num_retries": 0,
        "api_key": "must-not-use-configured-key",
        "extra_headers": {"Authorization": "Bearer must-not-override"},
        "tools": [
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}}}
        ],
    }


def check_chunks(chunks):
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == "OK"
    assert any(c.choices and c.choices[0].delta.reasoning_content == "Think" for c in chunks)
    assert any(c.choices and c.choices[0].delta.tool_calls for c in chunks)
    assert any(c.choices and c.choices[0].finish_reason == "tool_calls" for c in chunks)
    assert any(getattr(c, "usage", None) and c.usage.total_tokens == 15 for c in chunks)
    assert any(getattr(c, "usage", None) and c.usage.prompt_tokens_details.cache_creation_tokens == 1 for c in chunks)


@pytest.mark.parametrize("stream", [False, True])
def test_sync_completion_uses_signed_native_http_and_sse(stream):
    with httpx.Client(transport=httpx.MockTransport(provider_response)) as client:
        result = litellm.completion(client=HTTPHandler(client=client), **completion_args(stream))
        if stream:
            check_chunks(list(result))
        else:
            assert result.choices[0].message.content == "OK"
            assert result.choices[0].message.reasoning_content == "Think"
            assert result.choices[0].message.tool_calls[0].function.name == "lookup"
            assert result.usage.total_tokens == 15
            assert result.usage.prompt_tokens_details.cache_creation_tokens == 1
            assert litellm.completion_cost(completion_response=result, model=MODEL) == pytest.approx(0.00013838)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_async_completion_uses_signed_native_http_and_sse(stream):
    handler = AsyncHTTPHandler()
    await handler.client.aclose()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as client:
        handler.client = client
        result = await litellm.acompletion(client=handler, **completion_args(stream))
        if stream:
            check_chunks([c async for c in result])
        else:
            assert result.choices[0].message.content == "OK"
            assert result.choices[0].message.tool_calls[0].function.name == "lookup"
            assert result.usage.total_tokens == 15
            assert result.usage.prompt_tokens_details.cache_creation_tokens == 1
            assert litellm.completion_cost(completion_response=result, model=MODEL) == pytest.approx(0.00013838)


@pytest.mark.asyncio
async def test_async_transformation_matches_sync():
    config = BedrockRuntimeChatConfig()
    messages = [{"role": "user", "content": "Hi"}]
    params = {"aws_region_name": "us-east-1", "stream": True, "reasoning_effort": "low"}
    assert await config.async_transform_request(MODEL, messages, params, {}, {}) == config.transform_request(
        MODEL, messages, params, {}, {}
    )


def test_cache_usage_normalization_is_non_mutating():
    from litellm.llms.bedrock.chat.chat_completions_transformation import _native_usage
    from litellm.types.utils import Usage

    original = Usage(
        prompt_tokens=1000,
        completion_tokens=20,
        total_tokens=1020,
        prompt_tokens_details={"cached_tokens": 400, "cache_write_tokens": 100},
    )
    before = original.model_dump()
    usage = _native_usage(original)
    assert original.model_dump() == before
    response = litellm.ModelResponse(model=MODEL, usage=usage)
    assert litellm.completion_cost(completion_response=response, model=MODEL) == pytest.approx(0.003278)


def test_native_cache_usage_preserves_explicit_creation_and_old_usage():
    from litellm.llms.bedrock.chat.chat_completions_transformation import _native_usage
    from litellm.types.utils import Usage

    explicit = Usage(
        prompt_tokens=100,
        completion_tokens=10,
        prompt_tokens_details={"cache_creation_tokens": 20, "cache_write_tokens": 30},
    )
    assert _native_usage(explicit) is explicit
    ordinary = Usage(prompt_tokens=100, completion_tokens=10)
    assert _native_usage(ordinary) is ordinary


@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
@pytest.mark.parametrize("summary", ["auto", "detailed", "concise", None])
@pytest.mark.asyncio
async def test_reasoning_object_normalized_without_mutation(effort, summary):
    config = BedrockRuntimeChatConfig()
    params = {
        "reasoning_effort": {"effort": effort, "summary": summary},
        "stream_options": {"include_usage": True},
        "aws_region_name": "us-east-1",
    }
    before = deepcopy(params)
    messages = [{"role": "user", "content": "Hi"}]
    sync_body = config.transform_request(MODEL, messages, params, {}, {})
    async_body = await config.async_transform_request(MODEL, messages, params, {}, {})
    for body in (sync_body, async_body):
        assert body["reasoning_effort"] == effort
        assert "summary" not in body
        assert body["stream_options"] == {"include_usage": True}
        assert "aws_region_name" not in body
    assert params == before


@pytest.mark.parametrize("reasoning", [{"summary": "auto"}, {"effort": None, "summary": "detailed"}, {}])
@pytest.mark.asyncio
async def test_summary_only_leaves_runtime_default_reasoning(reasoning):
    config = BedrockRuntimeChatConfig()
    params = {"reasoning_effort": reasoning}
    messages = [{"role": "user", "content": "Hi"}]
    assert "reasoning_effort" not in config.transform_request(MODEL, messages, params, {}, {})
    assert "reasoning_effort" not in await config.async_transform_request(MODEL, messages, params, {}, {})


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("summary", ["auto", "detailed"])
@pytest.mark.asyncio
async def test_responses_summary_bridge_signs_scalar_effort(stream, summary):
    handler = AsyncHTTPHandler()
    await handler.client.aclose()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as client:
        handler.client = client
        result = await litellm.aresponses(
            model=MODEL,
            input="Hi",
            reasoning={"effort": "low", "summary": summary},
            max_output_tokens=128,
            stream=stream,
            aws_region_name="us-east-1",
            use_chat_completions_api=True,
            client=handler,
            num_retries=0,
        )
        if stream:
            events = [event async for event in result]
            assert any(event.type == "response.completed" for event in events)
        else:
            assert result.status == "completed"
