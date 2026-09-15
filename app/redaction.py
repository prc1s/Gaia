from typing import Any

SECRET_WORDS = ("token", "link", "password", "secret", "api_key")


def mask_email(email: str) -> str:
    """sara@gmail.com -> s***@gmail.com"""
    local, at, domain = email.partition("@")
    if not at or not local:
        return "***"
    return f"{local[0]}***@{domain}"


def _is_secret(key: str) -> bool:
    # Match "token" and "setup_token", but not "link_expires_at".
    key = key.lower()
    return any(key == w or key.endswith("_" + w) for w in SECRET_WORDS)


def redact(value: Any) -> Any:
    """Drop secret-named keys from dicts, recursively."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if not _is_secret(str(k))}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
