
"""Authentication and permission helpers for QueueX."""
from functools import wraps

from flask import jsonify, session
import hashlib
import hmac

from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from app.utils.db import execute_query, get_cursor


def _is_legacy_sha256(stored_hash: str) -> bool:
    """Return True when the hash looks like a 64-character SHA-256 digest."""
    return (
        isinstance(stored_hash, str)
        and len(stored_hash) == 64
        and all(c in "0123456789abcdefABCDEF" for c in stored_hash)
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a legacy SHA-256 or modern Werkzeug password hash."""
    if not isinstance(password, str) or not password:
        return False

    if not isinstance(stored_hash, str) or not stored_hash:
        return False

    if _is_legacy_sha256(stored_hash):
        candidate = hashlib.sha256(
            password.encode("utf-8")
        ).hexdigest()

        return hmac.compare_digest(
            candidate.lower(),
            stored_hash.lower(),
        )

    try:
        return check_password_hash(stored_hash, password)
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Generate a modern scrypt password hash."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")

    return generate_password_hash(password, method="scrypt")


def authenticate_user(username: str, password: str):
    """
    Authenticate an active staff account.

    Return a safe account dictionary on success, otherwise None.
    Never return the stored password hash.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    username = username.strip()

    if not username or not password:
        return None

    rows = execute_query(
        """
        SELECT
            sa.account_id,
            sa.staff_id,
            sa.username,
            sa.password_hash,
            sa.is_active AS account_active,
            sa.role_id,
            s.full_name,
            s.branch_id,
            s.is_active AS staff_active,
            r.name AS role_name
        FROM Staff_Account sa
        JOIN Staff s
            ON s.staff_id = sa.staff_id
        LEFT JOIN `Role` r
            ON r.role_id = sa.role_id
        WHERE sa.username = %s
        LIMIT 1
        """,
        (username,),
        fetch=True,
    )

    if not rows:
        return None

    account = rows[0]

    # Both the account and its linked staff record must be active.
    if account["account_active"] != 1:
        return None

    if account["staff_active"] != 1:
        return None

    # Fail closed for missing or unsupported roles.
    if account["role_name"] not in ("admin", "staff"):
        return None

    if not verify_password(password, account["password_hash"]):
        return None

    return {
        "account_id": account["account_id"],
        "staff_id": account["staff_id"],
        "username": account["username"],
        "full_name": account["full_name"],
        "branch_id": account["branch_id"],
        "role_id": account["role_id"],
        "role_name": account["role_name"],
    }


def upgrade_legacy_password_hash(
    account_id: int,
    old_hash: str,
    password: str,
) -> bool:
    """
    Conditionally upgrade a legacy hash after successful authentication.

    Returns True only when the database update changed one row.
    This function does not authenticate the user by itself.
    """
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return False

    if account_id <= 0 or not _is_legacy_sha256(old_hash):
        return False

    if not verify_password(password, old_hash):
        return False

    new_hash = hash_password(password)

    # Compare-and-update prevents overwriting a hash changed by another
    # request after the original hash was read.
    with get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE Staff_Account
            SET password_hash = %s
            WHERE account_id = %s
              AND password_hash = %s
              AND is_active = TRUE
            """,
            (new_hash, account_id, old_hash),
        )

        return cursor.rowcount == 1


def get_user_permissions(account_id: int) -> set[str]:
    """Return permissions for an active account's valid role."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return set()

    if account_id <= 0:
        return set()

    rows = execute_query(
        """
        SELECT p.name
        FROM Staff_Account sa
        JOIN `Role` r
            ON r.role_id = sa.role_id
        JOIN Role_Permission rp
            ON rp.role_id = r.role_id
        JOIN Permission p
            ON p.permission_id = rp.permission_id
        JOIN Staff s
            ON s.staff_id = sa.staff_id
        WHERE sa.account_id = %s
          AND sa.is_active = TRUE
          AND s.is_active = TRUE
          AND r.name IN ('admin', 'staff')
        """,
        (account_id,),
        fetch=True,
    )

    return {
        row["name"]
        for row in rows
        if isinstance(row.get("name"), str)
    }



def require_permission(permission: str):
    """Protect a route using current database-backed account permissions."""

    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            account = session.get("account")

            if not isinstance(account, dict):
                return jsonify({"error": "Authentication required"}), 401

            account_id = account.get("account_id")

            try:
                current_account = get_current_session_account(account_id)

                if current_account is None:
                    session.clear()
                    return jsonify({"error": "Authentication required"}), 401

                permissions = get_user_permissions(account_id)

            except Exception:
                # Fail closed if the database cannot verify authorization.
                return jsonify({"error": "Unable to verify authorization"}), 503

            if permission not in permissions:
                session["account"] = current_account
                session["permissions"] = sorted(permissions)
                return jsonify({"error": "Permission denied"}), 403

            session["account"] = current_account
            session["permissions"] = sorted(permissions)

            return view_function(*args, **kwargs)

        return wrapped

    return decorator


def get_current_session_account(account_id: int):
    """Return the current safe account record only if it remains active."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return None

    if account_id <= 0:
        return None

    rows = execute_query(
        """
        SELECT
            sa.account_id,
            sa.staff_id,
            sa.username,
            sa.is_active AS account_active,
            sa.role_id,
            s.full_name,
            s.branch_id,
            s.is_active AS staff_active,
            r.name AS role_name
        FROM Staff_Account sa
        JOIN Staff s ON s.staff_id = sa.staff_id
        JOIN `Role` r ON r.role_id = sa.role_id
        WHERE sa.account_id = %s
        LIMIT 1
        """,
        (account_id,),
        fetch=True,
    )

    if not rows:
        return None

    account = rows[0]

    if account["account_active"] != 1 or account["staff_active"] != 1:
        return None

    if account["role_name"] not in ("admin", "staff"):
        return None

    return {
        "account_id": account["account_id"],
        "staff_id": account["staff_id"],
        "username": account["username"],
        "full_name": account["full_name"],
        "branch_id": account["branch_id"],
        "role_id": account["role_id"],
        "role_name": account["role_name"],
    }
