from litellm.images.utils import ImageEditRequestUtils
from litellm.llms.openai.image_edit.transformation import OpenAIImageEditConfig


def test_openai_image_edit_keeps_output_options():
    params = ImageEditRequestUtils.get_requested_image_edit_optional_param(
        {"output_format": "jpeg", "output_compression": "30", "moderation": "low", "size": "1024x1024", "junk": 1}
    )
    assert params == {"output_format": "jpeg", "output_compression": "30", "moderation": "low", "size": "1024x1024"}
    out = ImageEditRequestUtils.get_optional_params_image_edit(
        model="gpt-image-2.5-flare", image_edit_provider_config=OpenAIImageEditConfig(), image_edit_optional_params=params
    )
    assert out["output_format"] == "jpeg" and out["output_compression"] == "30" and out["moderation"] == "low"
