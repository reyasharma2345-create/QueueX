from unittest.mock import patch

from app.utils.auth import (
    authenticate_user,
    get_user_permissions,
    hash_password,
    verify_password,
)


def _staff_record(**overrides):
    """Build a mock staff row matching the current staff table."""
    staff = {
        "staff_id": 1,
        "username": "staff1",
        "password_hash": hash_password("example-password"),
        "role": "staff",
        "full_name": "Test Staff",
        "branch_id": 1,
        "is_active": 1,
    }
    staff.update(overrides)
    return staff


def test_hash_password_and_verify_password():
    password = "example-password"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_verify_legacy_sha256_password():
    import hashlib

    password = "example-password"
    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

    assert verify_password(password, legacy_hash)
    assert not verify_password("wrong-password", legacy_hash)


def test_authenticate_user_rejects_missing_account():
    with patch("app.utils.auth.execute_query", return_value=[]):
        assert authenticate_user("unknown", "any-password") is None


def test_authenticate_user_rejects_inactive_account():
    staff = _staff_record(is_active=0)

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        assert authenticate_user("staff1", "example-password") is None


def test_authenticate_user_rejects_inactive_staff():
    # The current schema has a single staff.is_active field.
    staff = _staff_record(is_active=0)

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        assert authenticate_user("staff1", "example-password") is None


def test_authenticate_user_rejects_missing_role():
    staff = _staff_record(role=None)

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        assert authenticate_user("staff1", "example-password") is None


def test_authenticate_user_accepts_valid_staff():
    staff = _staff_record()

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        authenticated = authenticate_user("staff1", "example-password")

    assert authenticated is not None
    assert authenticated["staff_id"] == 1
    assert authenticated["username"] == "staff1"


def test_authenticate_user_rejects_wrong_password():
    staff = _staff_record()

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        assert authenticate_user("staff1", "incorrect-password") is None


def test_authenticate_user_rejects_empty_credentials():
    assert authenticate_user("", "example-password") is None
    assert authenticate_user("staff1", "") is None


def test_get_user_permissions_returns_staff_permissions():
    staff = {"role": "staff", "is_active": 1}

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        permissions = get_user_permissions(1)

    assert permissions == {"queue_operate"}


def test_get_user_permissions_returns_admin_permissions():
    staff = {"role": "admin", "is_active": 1}

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        permissions = get_user_permissions(1)

    assert permissions == {"queue_operate"}


def test_get_user_permissions_rejects_inactive_staff():
    staff = {"role": "staff", "is_active": 0}

    with patch("app.utils.auth.execute_query", return_value=[staff]):
        assert get_user_permissions(1) == set()


def test_get_user_permissions_rejects_missing_staff():
    with patch("app.utils.auth.execute_query", return_value=[]):
        assert get_user_permissions(1) == set()


def test_get_user_permissions_rejects_invalid_account_id():
    for invalid_id in (0, -1, True, "1"):
        assert get_user_permissions(invalid_id) == set()