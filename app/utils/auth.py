
"""Authentication and permission helpers for QueueX."""

from functools import wraps
import hashlib
import hmac

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

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
        candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate.lower(), stored_hash.lower())

    try:
        return check_password_hash(stored_hash, password)
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Generate a modern scrypt password hash."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")

    return generate_password_hash(password, method="scrypt")


# ============================================================
# STAFF AUTHENTICATION
# ============================================================

def authenticate_user(username: str, password: str):
    """Authenticate an active staff member using the existing staff table."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    username = username.strip()
    if not username or not password:
        return None

    rows = execute_query(
        """
        SELECT
            staff_id,
            username,
            password_hash,
            role,
            full_name,
            branch_id,
            is_active
        FROM staff
        WHERE username = %s
        LIMIT 1
        """,
        (username,),
        fetch=True,
    )

    if not rows:
        return None

    staff = rows[0]

    if staff["is_active"] != 1:
        return None

    role = str(staff["role"]).strip().lower()
    if role not in ("admin", "staff"):
        return None

    if not verify_password(password, staff["password_hash"]):
        return None

    # account_id is retained for compatibility with existing session routes.
    # In this schema, it maps to staff_id.
    return {
        "account_id": staff["staff_id"],
        "staff_id": staff["staff_id"],
        "username": staff["username"],
        "full_name": staff["full_name"],
        "branch_id": staff["branch_id"],
        "role_id": None,
        "role_name": role,
    }


def upgrade_legacy_password_hash(
    account_id: int,
    old_hash: str,
    password: str,
) -> bool:
    """Upgrade a legacy SHA-256 hash after successful password verification."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return False

    if account_id <= 0 or not _is_legacy_sha256(old_hash):
        return False

    if not verify_password(password, old_hash):
        return False

    new_hash = hash_password(password)

    # Update only if the stored hash has not changed since it was read.
    with get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE staff
            SET password_hash = %s
            WHERE staff_id = %s
              AND password_hash = %s
              AND is_active = TRUE
            """,
            (new_hash, account_id, old_hash),
        )
        return cursor.rowcount == 1


def get_user_permissions(account_id: int) -> set[str]:
    """Return permissions for an active staff member."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return set()

    if account_id <= 0:
        return set()

    rows = execute_query(
        """
        SELECT role, is_active
        FROM staff
        WHERE staff_id = %s
        LIMIT 1
        """,
        (account_id,),
        fetch=True,
    )

    if not rows or rows[0]["is_active"] != 1:
        return set()

    role = str(rows[0]["role"]).strip().lower()

    # These are the permissions required by the current staff routes.
    # Keep the set explicit; do not grant arbitrary permissions.
    if role in ("admin", "staff"):
        return {"queue_operate"}

    return set()


def get_current_session_account(account_id: int):
    """Return the current safe staff account if it remains active."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return None

    if account_id <= 0:
        return None

    rows = execute_query(
        """
        SELECT
            staff_id,
            username,
            full_name,
            branch_id,
            role,
            is_active
        FROM staff
        WHERE staff_id = %s
        LIMIT 1
        """,
        (account_id,),
        fetch=True,
    )

    if not rows:
        return None

    staff = rows[0]
    role = str(staff["role"]).strip().lower()

    if staff["is_active"] != 1 or role not in ("admin", "staff"):
        return None

    return {
        "account_id": staff["staff_id"],
        "staff_id": staff["staff_id"],
        "username": staff["username"],
        "full_name": staff["full_name"],
        "branch_id": staff["branch_id"],
        "role_id": None,
        "role_name": role,
    }


def require_permission(permission: str):
    """Protect a route using permissions verified against the database."""

    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            account = session.get("account")

            if not isinstance(account, dict):
                return jsonify({"error": "Authentication required"}), 401

            # Do not allow customer sessions to access staff routes.
            if account.get("role_name") not in ("admin", "staff"):
                return jsonify({"error": "Staff authentication required"}), 401

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

            session["account"] = current_account
            session["permissions"] = sorted(permissions)

            if permission not in permissions:
                return jsonify({"error": "Permission denied"}), 403

            return view_function(*args, **kwargs)

        return wrapped

    return decorator


# ============================================================
# CUSTOMER AUTHENTICATION
# ============================================================

def authenticate_customer(username: str, password: str):
    """Authenticate an active customer account without exposing its hash."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    username = username.strip()
    if not username or not password:
        return None

    rows = execute_query(
        """
        SELECT
            ca.account_id,
            ca.customer_id,
            ca.username,
            ca.password_hash,
            ca.is_active AS account_active,
            c.full_name
        FROM customer_account ca
        JOIN Customer c
            ON c.customer_id = ca.customer_id
        WHERE ca.username = %s
        LIMIT 1
        """,
        (username,),
        fetch=True,
    )

    if not rows:
        return None

    account = rows[0]

    if account["account_active"] != 1:
        return None

    if not verify_password(password, account["password_hash"]):
        return None

    return {
        "account_id": account["account_id"],
        "customer_id": account["customer_id"],
        "username": account["username"],
        "full_name": account["full_name"],
        "role_name": "customer",
    }


def get_customer_permissions(account_id: int) -> set[str]:
    """Return basic permissions only for an active customer account."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return set()

    if account_id <= 0:
        return set()

    rows = execute_query(
        """
        SELECT is_active
        FROM customer_account
        WHERE account_id = %s
        LIMIT 1
        """,
        (account_id,),
        fetch=True,
    )

    if not rows or rows[0]["is_active"] != 1:
        return set()

    return {
        "customer:read",
        "customer:manage_own",
    }


def get_current_session_customer(account_id: int):
    """Return the current safe customer record if the account remains active."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return None

    if account_id <= 0:
        return None

    rows = execute_query(
        """
        SELECT
            ca.account_id,
            ca.customer_id,
            ca.username,
            ca.is_active AS account_active,
            c.full_name
        FROM customer_account ca
        JOIN Customer c
            ON c.customer_id = ca.customer_id
        WHERE ca.account_id = %s
        LIMIT 1
        """,
        (account_id,),
        fetch=True,
    )

    if not rows:
        return None

    account = rows[0]

    if account["account_active"] != 1:
        return None

    return {
        "account_id": account["account_id"],
        "customer_id": account["customer_id"],
        "username": account["username"],
        "full_name": account["full_name"],
        "role_name": "customer",
    }


def require_customer_permission(permission: str):
    """Protect a route using verified customer permissions."""

    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            account = session.get("account")

            if not isinstance(account, dict):
                return jsonify({"error": "Authentication required"}), 401

            if account.get("role_name") != "customer":
                return jsonify(
                    {"error": "Customer authentication required"}
                ), 401

            account_id = account.get("account_id")

            try:
                current_account = get_current_session_customer(account_id)

                if current_account is None:
                    session.clear()
                    return jsonify({"error": "Authentication required"}), 401

                permissions = get_customer_permissions(account_id)

            except Exception:
                # Fail closed if the database cannot verify authorization.
                return jsonify({"error": "Unable to verify authorization"}), 503

            session["account"] = current_account
            session["permissions"] = sorted(permissions)

            if permission not in permissions:
                return jsonify({"error": "Permission denied"}), 403

            return view_function(*args, **kwargs)

        return wrapped

    return decorator
