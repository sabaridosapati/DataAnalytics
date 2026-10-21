import jwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    generate_code,
    hash_code,
    hash_password,
    password_problem,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("Passw0rd!")
    assert h != "Passw0rd!"
    assert verify_password("Passw0rd!", h)
    assert not verify_password("wrong", h)


def test_verify_password_bad_hash_is_false():
    assert verify_password("x", "not-a-bcrypt-hash") is False


@pytest.mark.parametrize(
    "pw, ok",
    [("short1", False), ("allletters", False), ("12345678", False), ("Passw0rd", True), ("Test@123", True), ("a1" * 40, False)],
)
def test_password_problem(pw, ok):
    assert (password_problem(pw) is None) is ok


def test_token_roundtrip_and_expiry():
    token = create_access_token(7, "admin", "s3cret", 5)
    payload = decode_access_token(token, "s3cret")
    assert payload["sub"] == "7" and payload["role"] == "admin"
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, "other")
    expired = create_access_token(7, "admin", "s3cret", -1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired, "s3cret")


def test_codes():
    code = generate_code()
    assert len(code) == 6 and code.isdigit()
    assert hash_code("123456", 1, "k") == hash_code("123456", 1, "k")
    assert hash_code("123456", 1, "k") != hash_code("123456", 2, "k")
