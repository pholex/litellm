"""
Codex replays prior assistant messages with `output_text` parts carrying `"logprobs": []`.
Volcengine Ark (doubao-seed-*) rejects that with `json: unknown field "logprobs"`, so
multi-turn / model-switch requests silently fell back. responses() now strips it.
"""

import copy
import http.server
import json
import threading

import pytest

import litellm

HIST = [
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Say hi"}]},
    {
        "type": "message",
        "role": "assistant",
        "id": "msg_1",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": "Hi!", "annotations": [], "logprobs": []},
            {"type": "refusal", "refusal": "n/a", "logprobs": "kept-on-non-output_text"},
        ],
    },
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Reply OK"}]},
]


class _Capture(http.server.BaseHTTPRequestHandler):
    body: dict = {}

    def do_POST(self):
        _Capture.body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        resp = json.dumps(
            {
                "id": "resp_1",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "m",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_2",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_logprobs_stripped_from_output_text_input(server):
    original = copy.deepcopy(HIST)
    litellm.responses(model="openai/doubao-seed-2-1-pro-260915", input=HIST, api_base=server, api_key="x")
    sent = _Capture.body["input"]
    assistant = next(i for i in sent if i.get("role") == "assistant")
    out_text = next(p for p in assistant["content"] if p["type"] == "output_text")
    assert "logprobs" not in out_text
    assert out_text["text"] == "Hi!" and out_text["annotations"] == []
    # non-output_text parts untouched
    refusal = next(p for p in assistant["content"] if p["type"] == "refusal")
    assert refusal["logprobs"] == "kept-on-non-output_text"
    # caller's input not mutated
    assert HIST == original


def test_string_input_and_no_logprobs_unchanged(server):
    litellm.responses(model="openai/m", input="hello", api_base=server, api_key="x")
    assert _Capture.body["input"] == "hello"
    clean = [HIST[0], HIST[2]]
    litellm.responses(model="openai/m", input=clean, api_base=server, api_key="x")
    assert _Capture.body["input"] == clean
