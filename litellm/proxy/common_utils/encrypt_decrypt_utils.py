import base64
import hashlib
import os
from typing import Final, Literal, Optional

from litellm._logging import verbose_proxy_logger


def _get_salt_key():
    from litellm.proxy.proxy_server import master_key

    salt_key = os.getenv("LITELLM_SALT_KEY", None)

    if salt_key is None:
        salt_key = master_key

    return salt_key


def encrypt_bearer_token(value: str, prefix: str) -> str:
    """Bind session ciphertext to its token kind using AES-GCM authenticated data."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt_key: Final = _get_salt_key()
    if not isinstance(salt_key, str) or not salt_key:
        raise ValueError("Set LITELLM_SALT_KEY or a master key to mint bearer tokens")
    nonce: Final = os.urandom(12)
    sealed: Final = nonce + AESGCM(hashlib.sha256(salt_key.encode()).digest()).encrypt(
        nonce, value.encode("utf-8"), prefix.encode("utf-8")
    )
    return prefix + base64.urlsafe_b64encode(sealed).decode("ascii").rstrip("=")


def decrypt_bearer_token(token: str, prefix: str) -> Optional[str]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not token.startswith(prefix):
        return None
    salt_key: Final = _get_salt_key()
    if not isinstance(salt_key, str) or not salt_key:
        return None
    encoded: Final = token.removeprefix(prefix)
    try:
        sealed: Final = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        return (
            AESGCM(hashlib.sha256(salt_key.encode()).digest())
            .decrypt(sealed[:12], sealed[12:], prefix.encode("utf-8"))
            .decode("utf-8")
        )
    except (ValueError, InvalidTag, UnicodeError):
        return None


def encrypt_value_helper(value: str, new_encryption_key: Optional[str] = None):
    signing_key = new_encryption_key or _get_salt_key()

    try:
        if isinstance(value, str):
            encrypted_value = encrypt_value(value=value, signing_key=signing_key)  # type: ignore
            # Use urlsafe_b64encode for URL-safe base64 encoding (replaces + with - and / with _)
            encrypted_value = base64.urlsafe_b64encode(encrypted_value).decode("utf-8")

            return encrypted_value

        verbose_proxy_logger.debug(
            f"Invalid value type passed to encrypt_value: {type(value)} for Value: {value}\n Value must be a string"
        )
        # if it's not a string - do not encrypt it and return the value
        return value
    except Exception as e:
        raise e


def decrypt_value_helper(
    value: str,
    key: str,  # this is just for debug purposes, showing the k,v pair that's invalid. not a signing key.
    exception_type: Literal["debug", "error"] = "error",
    return_original_value: bool = False,
):
    signing_key = _get_salt_key()

    try:
        if isinstance(value, str):
            # Try URL-safe base64 decoding first (new format)
            # Fall back to standard base64 decoding for backwards compatibility (old format)
            try:
                decoded_b64 = base64.urlsafe_b64decode(value)
            except Exception:
                # If URL-safe decoding fails, try standard base64 decoding for backwards compatibility
                decoded_b64 = base64.b64decode(value)

            value = decrypt_value(value=decoded_b64, signing_key=signing_key)  # type: ignore
            return value

        # if it's not str - do not decrypt it, return the value
        return value
    except Exception as e:
        error_message = f"Error decrypting value for key: {key}, Did your master_key/salt key change recently? \nError: {str(e)}\nSet permanent salt key - https://docs.litellm.ai/docs/proxy/prod#5-set-litellm-salt-key"
        if exception_type == "debug":
            verbose_proxy_logger.debug(error_message)
            return value if return_original_value else None

        verbose_proxy_logger.debug(f"Unable to decrypt value={value} for key: {key}, returning None")
        if return_original_value:
            return value
        else:
            verbose_proxy_logger.exception(error_message)
            # [Non-Blocking Exception. - this should not block decrypting other values]
            return None


def encrypt_value(value: str, signing_key: str):
    import hashlib

    import nacl.secret
    import nacl.utils

    # get 32 byte master key #
    hash_object = hashlib.sha256(signing_key.encode())
    hash_bytes = hash_object.digest()

    # initialize secret box #
    box = nacl.secret.SecretBox(hash_bytes)

    # encode message #
    value_bytes = value.encode("utf-8")

    encrypted = box.encrypt(value_bytes)

    return encrypted


def decrypt_value(value: bytes, signing_key: str) -> str:
    import hashlib

    import nacl.secret
    import nacl.utils

    # get 32 byte master key #
    hash_object = hashlib.sha256(signing_key.encode())
    hash_bytes = hash_object.digest()

    # initialize secret box #
    box = nacl.secret.SecretBox(hash_bytes)

    # Convert the bytes object to a string
    try:
        if len(value) == 0:
            return ""

        plaintext = box.decrypt(value)
        plaintext = plaintext.decode("utf-8")  # type: ignore
        return plaintext  # type: ignore
    except Exception as e:
        raise e
