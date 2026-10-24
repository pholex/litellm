from unittest.mock import patch

import pytest

import litellm


class _Stop(Exception):
    pass


def _captured_effort(**extra):
    seen = {}

    def fake_get_optional_params(*args, **kwargs):
        seen["reasoning_effort"] = kwargs.get("reasoning_effort")
        raise _Stop()

    with patch("litellm.main.get_optional_params", side_effect=fake_get_optional_params):
        with pytest.raises(Exception):
            litellm.completion(
                model="openai/gpt-x",
                api_key="sk-test",
                messages=[{"role": "user", "content": "hi"}],
                **extra,
            )
    return seen.get("reasoning_effort")


def test_reasoning_effort_map_rewrites_mapped_value():
    assert _captured_effort(reasoning_effort="none", reasoning_effort_map={"none": "low"}) == "low"


def test_reasoning_effort_map_leaves_other_values():
    assert _captured_effort(reasoning_effort="high", reasoning_effort_map={"none": "low"}) == "high"


def test_no_map_is_noop():
    assert _captured_effort(reasoning_effort="none") == "none"
