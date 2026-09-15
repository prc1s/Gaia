import pytest

from app.redaction import mask_email, redact


def test_redact_strips_secret_keys():
    data = {
        "token": "t",
        "setup_link": "l",
        "password": "p",
        "client_secret": "s",
        "api_key": "k",
        "sent": True,
        "link_expires_at": "2026-09-16",
    }
    assert redact(data) == {"sent": True, "link_expires_at": "2026-09-16"}


def test_redact_nested():
    data = {"a": {"access_token": "t", "b": [{"password": "p", "ok": 1}]}}
    assert redact(data) == {"a": {"b": [{"ok": 1}]}}


def test_redact_does_not_mutate_input():
    data = {"token": "t"}
    redact(data)
    assert data == {"token": "t"}


@pytest.mark.parametrize(
    "email, expected",
    [
        ("sara@gmail.com", "s***@gmail.com"),
        ("s@gmail.com", "s***@gmail.com"),
        ("not-an-email", "***"),
        ("@gmail.com", "***"),
    ],
)
def test_mask_email(email, expected):
    assert mask_email(email) == expected
