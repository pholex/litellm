"""
Codex built-in image_gen tool compatibility (tokenweave fork).

Codex sends:
  POST /images/generations  {"model","prompt","background":"opaque|transparent","quality":"auto","size":"auto"}
  POST /images/edits        JSON body {"images":[{"image_url":"data:image/png;base64,..."}], "prompt", "background", ...}
and reads `background` back from the response.
"""

import base64
import http.server
import json
import threading

import pytest
from fastapi import HTTPException

import litellm
from litellm.proxy.image_endpoints.endpoints import json_images_to_bytesio
from litellm.types.utils import ImageResponse

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# ---------- json_images_to_bytesio ----------


def test_json_images_data_url_decoded():
    url = "data:image/png;base64," + base64.b64encode(PNG_1x1).decode()
    out = json_images_to_bytesio([{"image_url": url}, {"image_url": {"url": url.replace("png", "jpeg", 1)}}])
    assert [b.getvalue() for b in out] == [PNG_1x1, PNG_1x1]
    assert out[0].name == "image_0.png"
    assert out[1].name == "image_1.jpg"


@pytest.mark.parametrize(
    "images",
    [
        [],
        "not-a-list",
        [{"file_id": "file-abc"}],
        [{"image_url": "https://example.com/a.png"}],
        [{"image_url": "data:image/png,rawnotbase64"}],
        [{"image_url": "data:image/png;base64,@@@not-base64@@@"}],
        [{}],
    ],
)
def test_json_images_rejected(images):
    with pytest.raises(HTTPException) as e:
        json_images_to_bytesio(images)
    assert e.value.status_code == 422


# ---------- response carries background ----------


def test_image_response_keeps_background():
    r = ImageResponse(data=[{"b64_json": "AA=="}], background="opaque", size="1024x1024")
    assert r.background == "opaque"
    assert r.model_dump()["background"] == "opaque"


# ---------- upstream request carries gpt-image params ----------


class _Capture(http.server.BaseHTTPRequestHandler):
    captured: dict = {}

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        _Capture.captured = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type", ""),
            "body": self.rfile.read(n),
        }
        body = json.dumps(
            {
                "created": 1,
                "background": "opaque",
                "data": [{"b64_json": "AA=="}],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"text_tokens": 1, "image_tokens": 0},
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def capture_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_image_generation_forwards_background(capture_server):
    r = litellm.image_generation(
        model="openai/gpt-image-2.5-sunburst",
        prompt="x",
        background="opaque",
        quality="auto",
        size="auto",
        api_base=capture_server,
        api_key="sk-test",
    )
    sent = json.loads(_Capture.captured["body"])
    assert _Capture.captured["path"].endswith("/images/generations")
    assert sent["background"] == "opaque"
    assert sent["quality"] == "auto" and sent["size"] == "auto"
    assert r.background == "opaque"


def test_image_edit_from_json_images_forwards_image_and_background(capture_server):
    url = "data:image/png;base64," + base64.b64encode(PNG_1x1).decode()
    r = litellm.image_edit(
        model="openai/gpt-image-2.5-sunburst",
        image=json_images_to_bytesio([{"image_url": url}]),
        prompt="add a hat",
        background="transparent",
        quality="auto",
        size="auto",
        api_base=capture_server,
        api_key="sk-test",
    )
    cap = _Capture.captured
    assert cap["path"].endswith("/images/edits")
    assert cap["content_type"].startswith("multipart/form-data")
    assert PNG_1x1 in cap["body"]
    assert b'name="background"' in cap["body"] and b"transparent" in cap["body"]
    assert r.background == "opaque"  # echoed from upstream response
