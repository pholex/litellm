import copy
import base64
import io
import json

import httpx
import pytest
from openai import AsyncOpenAI

import litellm
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.utils import convert_to_model_response_object

RESPONSE = {
    "created": 1794660000,
    "data": [
        {"url": "https://example.com/image.png", "size": "1024x1024", "revised_prompt": "A blue cup"},
        {"error": {"code": "1027", "message": "output image failed content safety check"}},
    ],
    "output_format": "png",
    "usage": {
        "generated_images": 1,
        "total_tokens": 9237,
        "input_tokens": 5141,
        "output_tokens": 4096,
        "input_tokens_details": {"text_tokens": 3091, "image_tokens": 2050},
        "output_tokens_details": {"image_tokens": 4096},
    },
    "request_id": "test-request",
}


@pytest.mark.parametrize(
    "model,image_rate,output_rate", [("MiniMax-H3-Image", 10, 25), ("MiniMax-H3-Image-Highspeed", 20, 50)]
)
@pytest.mark.parametrize("call_type", ["image_generation", "aimage_edit"])
def test_minimax_multimodal_token_cost(model, image_rate, output_rate, call_type):
    rates = {
        "input_cost_per_token": 15 / 6.8 / 1e6,
        "input_cost_per_image_token": image_rate / 6.8 / 1e6,
        "output_cost_per_token": output_rate / 6.8 / 1e6,
        "output_cost_per_image_token": output_rate / 6.8 / 1e6,
        "litellm_provider": "openai",
        "mode": "image_generation",
    }
    litellm.register_model({model: rates, "openai/" + model: rates})
    response = convert_to_model_response_object(
        response_object=copy.deepcopy(RESPONSE), response_type="image_generation"
    )
    actual = litellm.completion_cost(
        model=model, custom_llm_provider="openai", completion_response=response, call_type=call_type
    )
    assert actual == pytest.approx((3091 * 15 + 2050 * image_rate + 4096 * output_rate) / 6.8 / 1e6)
    assert response.data[1].provider_specific_fields["error"]["code"] == "1027"
    assert response.data[0].revised_prompt == "A blue cup"
    assert response.data[0].provider_specific_fields["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_minimax_generation_parameters_and_partial_results():
    def respond(request):
        body = json.loads(request.content)
        assert body["response_format"] == "b64_json"
        assert body["area"] == "1K"
        assert body["aspect_ratio"] == "16:9"
        assert body["disable_prompt_enhanced"] is True
        assert "max_retries" not in body
        return httpx.Response(200, json=copy.deepcopy(RESPONSE))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = AsyncOpenAI(api_key="synthetic", http_client=http, max_retries=0)
        response = await litellm.aimage_generation(
            model="openai/MiniMax-H3-Image",
            prompt="A cup",
            n=2,
            size="auto",
            response_format="b64_json",
            extra_body={"area": "1K", "aspect_ratio": "16:9", "disable_prompt_enhanced": True},
            client=client,
            api_key="synthetic",
            max_retries=0,
        )
    assert len(response.data) == 2
    assert response.usage.generated_images == 1
    assert response.request_id == "test-request"


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [False, True])
async def test_image_deployment_zero_retries_overrides_router_default(edit, monkeypatch):
    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "generation outcome unknown", "type": "server_error"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:

        class InjectedHandler(AsyncHTTPHandler):
            def create_client(self, **kwargs):
                return http

        if edit:
            monkeypatch.setattr(
                "litellm.llms.custom_httpx.llm_http_handler.get_async_httpx_client", lambda **kwargs: InjectedHandler()
            )
        client = InjectedHandler() if edit else AsyncOpenAI(api_key="synthetic", http_client=http, max_retries=0)
        router = litellm.Router(
            model_list=[
                {
                    "model_name": "minimax-test",
                    "litellm_params": {
                        "model": "openai/MiniMax-H3-Image",
                        "api_key": "synthetic",
                        "api_base": "https://minimax.test/v1",
                        "max_retries": 0,
                        "num_retries": 0,
                    },
                }
            ],
            num_retries=2,
            retry_after=0,
            allowed_fails=100,
        )
        args = {"model": "minimax-test", "prompt": "A cup", "client": client}
        if edit:
            args["image"] = io.BytesIO(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSyoAAAAASUVORK5CYII="
                )
            )
        with pytest.raises(litellm.InternalServerError):
            if edit:
                await router.aimage_edit(**args)
            else:
                await router.aimage_generation(**args)
        assert len(requests) == 1
        router.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,image_rate,output_rate", [("MiniMax-H3-Image", 10, 25), ("MiniMax-H3-Image-Highspeed", 20, 50)]
)
@pytest.mark.parametrize("edit", [False, True])
async def test_router_registered_deployment_image_token_pricing(model, image_rate, output_rate, edit, monkeypatch):
    rates = {
        "input_cost_per_token": 15 / 6.8 / 1e6,
        "input_cost_per_image_token": image_rate / 6.8 / 1e6,
        "output_cost_per_token": output_rate / 6.8 / 1e6,
        "output_cost_per_image_token": output_rate / 6.8 / 1e6,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=copy.deepcopy(RESPONSE)))
    ) as http:
        client = AsyncOpenAI(api_key="synthetic", http_client=http, max_retries=0)
        router = litellm.Router(
            model_list=[
                {
                    "model_name": "h3-priced",
                    "litellm_params": {
                        "model": "openai/" + model,
                        "api_base": "https://minimax.test/v1",
                        "api_key": "synthetic",
                        "max_retries": 0,
                        "num_retries": 0,
                        **rates,
                    },
                    "model_info": {"mode": "image_generation", "hide_from_model_list": True},
                }
            ],
            num_retries=0,
        )
        if edit:

            class InjectedHandler(AsyncHTTPHandler):
                def create_client(self, **kwargs):
                    return http

            monkeypatch.setattr(
                "litellm.llms.custom_httpx.llm_http_handler.get_async_httpx_client", lambda **kwargs: InjectedHandler()
            )
            image = io.BytesIO(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSyoAAAAASUVORK5CYII="
                )
            )
            response = await router.aimage_edit(model="h3-priced", prompt="A cup", image=image)
        else:
            response = await router.aimage_generation(model="h3-priced", prompt="A cup", client=client)
        assert response._hidden_params["response_cost"] == pytest.approx(
            (3091 * 15 + 2050 * image_rate + 4096 * output_rate) / 6.8 / 1e6
        )
        router.reset()
