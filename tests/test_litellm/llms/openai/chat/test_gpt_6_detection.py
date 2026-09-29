"""GPT-6 routes through the GPT-5 reasoning config (tokenweave fork).

Bedrock-hosted GPT-6 (and GPT-5.x under Bedrock names) must get the GPT-5
param handling: max_tokens -> max_completion_tokens, and the tools +
reasoning_effort -> Responses bridge flag. Before, "us.openai.gpt-6-astra"
fell through to the plain GPT config and max_tokens was sent verbatim (400).
"""
import pytest

import litellm
from litellm.llms.openai.chat.gpt_5_transformation import OpenAIGPT5Config


@pytest.mark.parametrize(
    "model",
    [
        "us.openai.gpt-6-astra",
        "openai/us.openai.gpt-6-astra",
        "global.openai.gpt-6-sol",
        "openai/global.openai.gpt-6-luna",
        "openai.gpt-6-sol",
        "gpt-6-astra",
    ],
)
def test_gpt_6_is_gpt_5_family(model):
    assert OpenAIGPT5Config.is_model_gpt_5_model(model)
    assert OpenAIGPT5Config.is_model_gpt_5_4_plus_model(model)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai.gpt-5.6-sol", True),
        ("openai/openai.gpt-5.6-terra", True),
        ("gpt-5.4", True),
        ("gpt-5.1", False),
        ("openai.gpt-5.1", False),
        ("gpt-5", False),
    ],
)
def test_gpt_5_4_plus_accepts_bedrock_names(model, expected):
    assert OpenAIGPT5Config.is_model_gpt_5_4_plus_model(model) is expected


@pytest.mark.parametrize(
    "model", ["gpt-4o", "openai.gpt-oss-120b-1:0", "gpt-5-chat-latest", "xai.grok-4.7"]
)
def test_unrelated_models_unchanged(model):
    assert not OpenAIGPT5Config._is_gpt_6_family(model)
    assert OpenAIGPT5Config.is_model_gpt_5_4_plus_model(model) is False


def test_gpt_6_config_selected_and_max_tokens_mapped():
    config = litellm.ProviderConfigManager.get_provider_chat_config(
        model="us.openai.gpt-6-astra", provider=litellm.LlmProviders.OPENAI
    )
    assert isinstance(config, OpenAIGPT5Config)
    optional = config.map_openai_params(
        non_default_params={"max_tokens": 50, "reasoning_effort": "xhigh"},
        optional_params={},
        model="us.openai.gpt-6-astra",
        drop_params=True,
    )
    assert optional.get("max_completion_tokens") == 50
    assert "max_tokens" not in optional
    # xhigh is supported by every GPT-6 model and must not be dropped
    assert optional.get("reasoning_effort") == "xhigh"


def test_gpt_6_drops_unsupported_temperature_with_drop_params():
    optional = OpenAIGPT5Config().map_openai_params(
        non_default_params={"temperature": 0.3},
        optional_params={},
        model="global.openai.gpt-6-sol",
        drop_params=True,
    )
    assert "temperature" not in optional
