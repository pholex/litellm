"""
Deployment-level per-image pricing (model_info.output_cost_per_image / input_cost_per_image)
for non-gpt-image models behind the openai provider, e.g. Volcengine Ark Seedream.
Before this, such calls fell into the DALL-E size/quality lookup, raised, and logged no cost.
"""

import asyncio
import http.server
import json
import threading
from types import SimpleNamespace

import pytest

import litellm
from litellm.cost_calculator import _deployment_per_image_cost
from litellm.types.utils import ImageResponse

OUT = 0.01764705882  # ¥0.12 / 6.8
IN = 0.002941176471  # ¥0.02 / 6.8


def _logging_obj(model_info):
    return SimpleNamespace(litellm_params={"metadata": {"model_info": model_info}})


def _resp(usage=None, n=1):
    r = ImageResponse(data=[{"url": f"https://x/{i}.png"} for i in range(n)])
    if usage is not None:
        r.usage = usage
    return r


def test_uses_ark_usage_counts():
    r = _resp(usage={"input_images": 2, "generated_images": 17, "output_tokens": 1, "total_tokens": 1}, n=17)
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj({"output_cost_per_image": OUT, "input_cost_per_image": IN}))
    assert cost == pytest.approx(17 * OUT + 2 * IN)


def test_falls_back_to_len_data_without_usage_counts():
    r = _resp(n=3)
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj({"output_cost_per_image": OUT}))
    assert cost == pytest.approx(3 * OUT)


def test_router_model_id_cost_map_entry(monkeypatch):
    monkeypatch.setitem(litellm.model_cost, "dep-123", {"output_cost_per_image": OUT})
    assert _deployment_per_image_cost(_resp(n=2), router_model_id="dep-123") == pytest.approx(2 * OUT)


@pytest.mark.parametrize(
    "model_info",
    [None, {}, {"input_cost_per_image": IN}, {"input_cost_per_token": 1e-6}],
)
def test_not_applied_without_output_cost_per_image(model_info):
    obj = _logging_obj(model_info) if model_info is not None else None
    assert _deployment_per_image_cost(_resp(n=1), litellm_logging_obj=obj) is None


class _Ark(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(
            {
                "model": "doubao-seedream-5-0-flash-260915",
                "created": 1,
                "data": [{"url": "https://x/y.png", "size": "1872x1248"}],
                "usage": {"input_images": 1, "generated_images": 1, "output_tokens": 9126, "total_tokens": 9126},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_router_image_generation_records_response_cost():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Ark)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        router = litellm.Router(
            model_list=[
                {
                    "model_name": "doubao-seedream-5-flash",
                    "litellm_params": {
                        "model": "openai/doubao-seedream-5-0-flash-260915",
                        "api_key": "x",
                        "api_base": f"http://127.0.0.1:{srv.server_port}/v1",
                    },
                    "model_info": {"mode": "image_generation", "input_cost_per_image": IN, "output_cost_per_image": OUT},
                }
            ]
        )
        resp = asyncio.run(router.aimage_generation(model="doubao-seedream-5-flash", prompt="p"))
        assert resp._hidden_params.get("response_cost") == pytest.approx(OUT + IN)
    finally:
        srv.shutdown()
