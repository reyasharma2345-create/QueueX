
import hashlib
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app.utils.auth import (
    authenticate_user,
    get_user_permissions,
    hash_password,
    verify_password,
)


def test_verify_legacy_sha256_password():
    password = "example-password"
    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

    assert verify_password(password, legacy_hash)
    assert not verify_password("wrong-password", legacy_hash)


def test_verify_modern_scrypt_password():
    password = "example-password"
    modern_hash = generate_password_hash(password, method="scrypt")

    assert verify_password(password, modern_hash)
    assert not verify_password("wrong-password", modern_hash)


def test_reject_invalid_or_empty_password_values():
    assert not verify_password("", "a" * 64)
    assert not verify_password("password", "")
    assert not verify_password(None, "a" * 64)
    assert not verify_password("password", None)


def test_hash_password_uses_scrypt():
    password_hash = hash_password("example-password")

    assert password_hash.startswith("scrypt:")
    assert verify_password("example-password", password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_authenticate_user_rejects_missing_account():
    with patch("app.utils.auth.execute_query", return_value=[]):
        assert authenticate_user("unknown", "any-password") is None


def test_authenticate_user_rejects_inactive_account():
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "password_hash": hash_password("example-password"),
        "account_active": 0,
        "role_id": 2,
        "full_name": "Test Staff",
        "branch_id": 1,
        "staff_active": 1,
        "role_name": "staff",
    }

    with patch("app.utils.auth.execute_query", return_value=[account]):
        assert authenticate_user("staff1", "example-password") is None


def test_authenticate_user_rejects_inactive_staff():
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "password_hash": hash_password("example-password"),
        "account_active": 1,
        "role_id": 2,
        "full_name": "Test Staff",
        "branch_id": 1,
        "staff_active": 0,
        "role_name": "staff",
    }

    with patch("app.utils.auth.execute_query", return_value=[account]):
        assert authenticate_user("staff1", "example-password") is None


def test_authenticate_user_rejects_missing_role():
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "password_hash": hash_password("example-password"),
        "account_active": 1,
        "role_id": None,
        "full_name": "Test Staff",
        "branch_id": 1,
        "staff_active": 1,
        "role_name": None,
    }

    with patch("app.utils.auth.execute_query", return_value=[account]):
        assert authenticate_user("staff1", "example-password") is None


def test_get_user_permissions_returns_permission_names():
    with patch(
        "app.utils.auth.execute_query",
        return_value=[
            {"name": "queue_operate"},
            {"name": "staff_view"},
        ],
    ):
        assert get_user_permissions(1) == {
            "queue_operate",
            "staff_view",
        }


def test_get_user_permissions_rejects_invalid_account_id():
    assert get_user_permissions(0) == set()
    assert get_user_permissions(-1) == set()
    assert get_user_permissions(True) == set()
    assert get_user_permissions("1") == set()
