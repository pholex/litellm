from typing import Any, Dict, List, Literal, Optional, Union

from typing_extensions import TypedDict

from litellm.types.utils import FileTypes


class ImageEditOptionalRequestParams(TypedDict, total=False):
    """
    TypedDict for Optional parameters supported by OpenAI's image edit API.

    Params here: https://platform.openai.com/docs/api-reference/images/createEdit
    """

    background: Optional[Literal["transparent", "opaque", "auto"]]
    input_fidelity: Optional[Literal["high", "low"]]
    mask: Optional[str]
    n: Optional[int]
    quality: Optional[Literal["high", "medium", "low", "standard", "auto"]]
    response_format: Optional[Literal["url", "b64_json"]]
    size: Optional[str]
    user: Optional[str]
    imageConfig: Optional[Dict[str, Any]]
    # GPT Image output options (also valid on /images/edits); were silently dropped before.
    output_format: Optional[Literal["png", "jpeg", "webp"]]
    output_compression: Optional[int]
    moderation: Optional[Literal["auto", "low"]]
    thinkingConfig: Optional[Dict[str, Any]]  # Gemini thinking image models (gemini-nano-banana-2.1)


class ImageEditRequestParams(ImageEditOptionalRequestParams, total=False):
    """
    TypedDict for request parameters supported by OpenAI's image edit API.

    Params here: https://platform.openai.com/docs/api-reference/images/createEdit
    """

    image: FileTypes
    prompt: str
    model: Optional[str]
