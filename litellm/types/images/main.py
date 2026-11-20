from typing import Literal

from typing_extensions import TypedDict

from litellm.types.utils import FileTypes


class ImageEditOptionalRequestParams(TypedDict, total=False):
    """
    TypedDict for Optional parameters supported by OpenAI's image edit API.

    Params here: https://platform.openai.com/docs/api-reference/images/createEdit
    """

    background: Literal["transparent", "opaque", "auto"] | None
    complexity: Literal["low", "medium", "high"] | None
    markup: FileTypes | None
    safety_identifier: str | None
    moderation: Literal["auto", "low"] | None
    output_compression: int | str | None
    output_format: Literal["png", "jpeg", "webp", "svg", "gif"] | None
    input_fidelity: Literal["high", "low"] | None
    mask: str | None
    n: int | None
    quality: Literal["high", "medium", "low", "standard", "auto"] | None
    response_format: Literal["url", "b64_json"] | None
    size: str | None
    user: str | None
    imageConfig: dict[str, object] | None
    thinkingConfig: dict[str, object] | str | None


class ImageEditRequestParams(ImageEditOptionalRequestParams, total=False):
    """
    TypedDict for request parameters supported by OpenAI's image edit API.

    Params here: https://platform.openai.com/docs/api-reference/images/createEdit
    """

    image: FileTypes
    prompt: str
    model: str | None
