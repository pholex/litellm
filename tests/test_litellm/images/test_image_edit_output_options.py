from litellm.images.utils import ImageEditRequestUtils
from litellm.llms.openai.image_edit.transformation import OpenAIImageEditConfig


def test_openai_image_edit_keeps_output_options():
    params = ImageEditRequestUtils.get_requested_image_edit_optional_param(
        {"output_format": "jpeg", "output_compression": "30", "moderation": "low", "size": "1024x1024", "junk": 1}
    )
    assert params == {"output_format": "jpeg", "output_compression": "30", "moderation": "low", "size": "1024x1024"}
    out = ImageEditRequestUtils.get_optional_params_image_edit(
        model="gpt-image-2.5-flare",
        image_edit_provider_config=OpenAIImageEditConfig(),
        image_edit_optional_params=params,
    )
    assert out["output_format"] == "jpeg" and out["output_compression"] == "30" and out["moderation"] == "low"


def test_openai_image_edit_forwards_complexity_and_markup():
    import io
    from litellm.types.router import GenericLiteLLMParams

    overlay = io.BytesIO(b"overlay")
    params = ImageEditRequestUtils.get_requested_image_edit_optional_param(
        {"complexity": "low", "markup": overlay, "safety_identifier": "test-user"}
    )
    config = OpenAIImageEditConfig()
    mapped = ImageEditRequestUtils.get_optional_params_image_edit(
        model="gpt-image-2.6-flare", image_edit_provider_config=config, image_edit_optional_params=params
    )
    data, files = config.transform_image_edit_request(
        model="gpt-image-2.6-flare",
        prompt="test",
        image=io.BytesIO(b"original"),
        image_edit_optional_request_params=mapped,
        litellm_params=GenericLiteLLMParams(),
        headers={},
    )
    assert data["complexity"] == "low"
    assert data["safety_identifier"] == "test-user"
    assert "markup" not in data
    assert next(value for name, value in files if name == "markup")[1] is overlay


def test_openai_image_edit_rejects_mask_with_markup():
    import io
    import pytest
    from litellm.llms.openai.common_utils import OpenAIError
    from litellm.types.router import GenericLiteLLMParams

    with pytest.raises(OpenAIError, match="markup cannot be combined with mask"):
        OpenAIImageEditConfig().transform_image_edit_request(
            model="gpt-image-2.6-flare",
            prompt="test",
            image=io.BytesIO(b"original"),
            image_edit_optional_request_params={"markup": io.BytesIO(b"overlay"), "mask": io.BytesIO(b"mask")},
            litellm_params=GenericLiteLLMParams(),
            headers={},
        )
