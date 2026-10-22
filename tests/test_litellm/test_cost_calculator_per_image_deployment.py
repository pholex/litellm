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


HI = 0.08955223881  # ¥0.6 / 6.7
LO = 0.04477611940  # ¥0.3 / 6.7
TIERED = {"output_cost_per_image": HI, "output_cost_per_image_tiers": [{"max_pixels": 2610000, "output_cost_per_image": LO}],
          "input_cost_per_image": IN, "free_input_images": 1}


@pytest.mark.parametrize("size,expected", [("1600x1600", LO), ("1616x1616", HI), ("2048x2048", HI), ("1615x1616", LO), ("1500x1700", LO)])
def test_pixel_tiers_from_response_size(size, expected):
    r = _resp(n=1)
    r.size = size
    assert _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(TIERED)) == pytest.approx(expected)


def test_pixel_tiers_from_request_size_when_response_has_none():
    obj = SimpleNamespace(litellm_params={"metadata": {"model_info": TIERED}}, model_call_details={"optional_params": {"size": "1536x1536"}})
    assert _deployment_per_image_cost(_resp(n=2), litellm_logging_obj=obj) == pytest.approx(2 * LO)


def test_unknown_size_uses_base_price():
    obj = SimpleNamespace(litellm_params={"metadata": {"model_info": TIERED}}, model_call_details={"optional_params": {"size": "2K"}})
    assert _deployment_per_image_cost(_resp(n=1), litellm_logging_obj=obj) == pytest.approx(HI)


def test_first_input_image_free():
    r = _resp(usage={"input_images": 3, "generated_images": 1, "output_tokens": 1, "total_tokens": 1}, n=1)
    r.size = "2048x2048"
    assert _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(TIERED)) == pytest.approx(HI + 2 * IN)
    r1 = _resp(usage={"input_images": 1, "generated_images": 1, "output_tokens": 1, "total_tokens": 1}, n=1)
    r1.size = "1600x1600"
    assert _deployment_per_image_cost(r1, litellm_logging_obj=_logging_obj(TIERED)) == pytest.approx(LO)


def test_image_response_takes_size_from_ark_items():
    r = ImageResponse(data=[{"url": "https://x/y.png", "size": "1872x1248"}])
    assert r.size == "1872x1248"
    assert ImageResponse(data=[{"url": "u"}], size="1024x1024").size == "1024x1024"


def test_router_tiered_cost_end_to_end():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Ark)  # Ark stub returns data[].size 1872x1248 (2.34 MP), 1 input image
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        router = litellm.Router(
            model_list=[
                {
                    "model_name": "doubao-seedream-5-pro",
                    "litellm_params": {
                        "model": "openai/doubao-seedream-5-0-pro-260628",
                        "api_key": "x",
                        "api_base": f"http://127.0.0.1:{srv.server_port}/v1",
                    },
                    "model_info": {"mode": "image_generation", **TIERED},
                }
            ]
        )
        resp = asyncio.run(router.aimage_generation(model="doubao-seedream-5-pro", prompt="p"))
        assert resp.size == "1872x1248"
        assert resp._hidden_params.get("response_cost") == pytest.approx(LO)  # low tier, first input image free
    finally:
        srv.shutdown()


@pytest.mark.parametrize("out_tokens,expected", [(9126, LO), (16224, HI)])  # measured: Ark 1.5K -> 9126, 2K -> 16224
def test_pixel_tiers_from_ark_output_tokens(out_tokens, expected):
    r = _resp(usage={"input_images": 0, "generated_images": 1, "output_tokens": out_tokens, "total_tokens": out_tokens}, n=1)
    obj = SimpleNamespace(litellm_params={"metadata": {"model_info": TIERED}}, model_call_details={"optional_params": {"size": "2K"}})
    assert _deployment_per_image_cost(r, litellm_logging_obj=obj) == pytest.approx(expected)


# ---- layer decomposition: every output billed by its own pixels at the layer rate ----
LAYER_HI = 0.04477611940  # ¥0.30 / 6.7 (> 2.61 MP)
LAYER_LO = 0.02238805970  # ¥0.15 / 6.7 (<= 2.61 MP)
LAYERED = {**TIERED, "output_cost_per_layer": LAYER_HI,
           "output_cost_per_layer_tiers": [{"max_pixels": 2610000, "output_cost_per_layer": LAYER_LO}]}


def _layer_resp(sizes, input_images=1):
    items = [{"b64_json": "x", "size": s, "output_format": "png", "z_index": i} for i, s in enumerate(sizes)]
    r = ImageResponse(data=items)
    r.usage = {"input_images": input_images, "generated_images": len(sizes), "output_tokens": 1, "total_tokens": 1}
    return r


def test_layers_billed_per_item_at_layer_rate():
    r = _layer_resp(["2000x2000", "1473x383", "889x570"])  # base above the tier, two small layers
    assert r.data[1].provider_specific_fields["z_index"] == 1
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(LAYERED))
    assert cost == pytest.approx(LAYER_HI + 2 * LAYER_LO)


def test_layers_input_images_still_charged_after_free_one():
    r = _layer_resp(["1024x1024", "300x200"], input_images=3)
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(LAYERED))
    assert cost == pytest.approx(2 * LAYER_LO + 2 * IN)


def test_layers_fall_back_to_image_tiers_without_layer_price():
    r = _layer_resp(["2000x2000", "1473x383"])
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(TIERED))
    assert cost == pytest.approx(HI + LO)


def test_layer_item_without_size_uses_top_layer_price():
    r = ImageResponse(data=[{"b64_json": "x", "z_index": 0, "size": "1024x1024"}, {"b64_json": "y", "z_index": 1}])
    cost = _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(LAYERED))
    assert cost == pytest.approx(LAYER_LO + LAYER_HI)


def test_plain_response_is_not_treated_as_layers():
    r = _resp(n=2)
    r.size = "1024x1024"
    assert _deployment_per_image_cost(r, litellm_logging_obj=_logging_obj(LAYERED)) == pytest.approx(2 * LO)
