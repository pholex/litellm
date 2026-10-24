from unittest.mock import patch

from litellm.utils import inline_image_urls_in_messages


def test_inline_image_urls_replaces_remote_urls_only():
    msgs = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png", "detail": "low"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": "https://example.com/b.jpg"},
                {"type": "text", "text": "hi"},
            ],
        },
    ]
    with patch(
        "litellm.litellm_core_utils.prompt_templates.image_handling.convert_url_to_base64",
        side_effect=lambda u: "data:image/png;base64,X" + u[-5:],
    ) as conv:
        out = inline_image_urls_in_messages(msgs)
    assert conv.call_count == 2
    parts = out[1]["content"]
    assert parts[0]["image_url"] == {"url": "data:image/png;base64,Xa.png", "detail": "low"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert parts[2]["image_url"] == {"url": "data:image/png;base64,Xb.jpg"}
    # caller's messages are not mutated
    assert msgs[1]["content"][0]["image_url"]["url"] == "https://example.com/a.png"


def test_inline_image_urls_noop_returns_same_list():
    msgs = [{"role": "user", "content": "plain"}]
    assert inline_image_urls_in_messages(msgs) is msgs
