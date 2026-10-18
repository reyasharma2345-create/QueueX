
"""Authentication routes for QueueX."""

from flask import Blueprint, current_app, jsonify, request, session
from mysql.connector import IntegrityError

from app.utils.auth import (
    authenticate_user,
    authenticate_customer,
    get_current_session_account,
    get_current_session_customer,
    get_user_permissions,
    get_customer_permissions,
    hash_password,
)
from app.utils.db import get_cursor


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


# ============================================================
# STAFF AUTH ROUTES
# ============================================================

@auth_bp.route("/login", methods=["POST"])
def login():
    """Authenticate staff and establish a session."""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "JSON request body required"}), 400

    username = data.get("username")
    password = data.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({"error": "Username and password are required"}), 400

    account = authenticate_user(username, password)

    if account is None:
        return jsonify({"error": "Invalid username or password"}), 401

    permissions = get_user_permissions(account["account_id"])

    if not permissions:
        return jsonify({"error": "Account has no assigned permissions"}), 403

    # Prevent session fixation.
    session.clear()
    session.permanent = True
    session["account"] = account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "success": True,
        "account": account,
        "permissions": sorted(permissions),
    }), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the current session."""
    session.clear()
    return jsonify({"success": True}), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    """Verify the current staff session against the database."""
    account = session.get("account")

    if not isinstance(account, dict):
        return jsonify({"error": "Authentication required"}), 401

    # Do not accept a customer session as a staff session.
    if account.get("role_name") == "customer":
        return jsonify({"error": "Staff authentication required"}), 401

    account_id = account.get("account_id")

    try:
        current_account = get_current_session_account(account_id)

        if current_account is None:
            session.clear()
            return jsonify({"error": "Authentication required"}), 401

        permissions = get_user_permissions(account_id)

        if not permissions:
            session.clear()
            return jsonify({"error": "Account has no assigned permissions"}), 403

    except Exception:
        current_app.logger.exception("Unable to verify staff session")
        return jsonify({"error": "Unable to verify authorization"}), 503

    session["account"] = current_account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "account": current_account,
        "permissions": sorted(permissions),
    }), 200


# ============================================================
# CUSTOMER REGISTRATION
# ============================================================

@auth_bp.route("/customer/register", methods=["POST"])
def customer_register():
    """Create a customer profile and login account atomically."""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "JSON request body required"}), 400

    full_name = data.get("full_name")
    username = data.get("username")
    password = data.get("password")
    phone = data.get("phone")
    email = data.get("email")

    # Validate required fields.
    if not isinstance(full_name, str) or not full_name.strip():
        return jsonify({"error": "Full name is required"}), 400

    if not isinstance(username, str):
        return jsonify({"error": "Username is required"}), 400

    if not isinstance(password, str):
        return jsonify({"error": "Password is required"}), 400

    full_name = full_name.strip()
    username = username.strip()

    if len(full_name) > 100:
        return jsonify({"error": "Full name cannot exceed 100 characters"}), 400

    if not username or len(username) > 50:
        return jsonify({"error": "Username must be 1–50 characters"}), 400

    # Reject whitespace in usernames.
    if any(character.isspace() for character in username):
        return jsonify({"error": "Username cannot contain spaces"}), 400

    if len(password) < 8 or len(password) > 128:
        return jsonify({
            "error": "Password must contain 8–128 characters"
        }), 400

    # Validate optional contact information.
    if phone is not None:
        if not isinstance(phone, str) or len(phone.strip()) > 20:
            return jsonify({
                "error": "Phone must be 20 characters or fewer"
            }), 400

        phone = phone.strip() or None

    if email is not None:
        if not isinstance(email, str) or len(email.strip()) > 100:
            return jsonify({
                "error": "Email must be 100 characters or fewer"
            }), 400

        email = email.strip() or None

    if email and (
        "@" not in email
        or email.startswith("@")
        or email.endswith("@")
        or any(character.isspace() for character in email)
    ):
        return jsonify({"error": "Enter a valid email address"}), 400

    # Hash before storing. Never store the raw password.
    try:
        password_hash = hash_password(password)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid password"}), 400

    # Create the customer and login account in one transaction.
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO Customer (full_name, phone, email)
                VALUES (%s, %s, %s)
                """,
                (full_name, phone, email),
            )
            customer_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO customer_account
                    (customer_id, username, password_hash, is_active)
                VALUES (%s, %s, %s, 1)
                """,
                (customer_id, username, password_hash),
            )
            account_id = cursor.lastrowid

    except IntegrityError as error:
        # MySQL duplicate-key violation.
        if getattr(error, "errno", None) == 1062:
            return jsonify({
                "error": "That username is already registered"
            }), 409

        current_app.logger.exception("Customer registration database error")
        return jsonify({"error": "Unable to register customer"}), 500

    except Exception:
        current_app.logger.exception("Customer registration failed")
        return jsonify({"error": "Unable to register customer"}), 500

    account = {
        "account_id": account_id,
        "customer_id": customer_id,
        "username": username,
        "full_name": full_name,
        "role_name": "customer",
    }

    # If permissions cannot be loaded, the account still exists.
    try:
        permissions = get_customer_permissions(account_id)

        if not permissions:
            current_app.logger.error(
                "Customer account %s was created but could not be authorized",
                account_id,
            )
            return jsonify({
                "success": True,
                "requires_login": True,
                "message": "Account created. Please sign in.",
            }), 201

    except Exception:
        current_app.logger.exception(
            "Customer registered but session authorization failed"
        )
        return jsonify({
            "success": True,
            "requires_login": True,
            "message": "Account created. Please sign in.",
        }), 201

    # Establish the customer session after successful database writes.
    session.clear()
    session.permanent = True
    session["account"] = account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "success": True,
        "account": account,
        "permissions": sorted(permissions),
    }), 201


# ============================================================
# CUSTOMER AUTH ROUTES
# ============================================================

@auth_bp.route("/customer/login", methods=["POST"])
def customer_login():
    """Authenticate customer and establish a session."""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "JSON request body required"}), 400

    username = data.get("username")
    password = data.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({"error": "Username and password are required"}), 400

    account = authenticate_customer(username, password)

    if account is None:
        return jsonify({"error": "Invalid username or password"}), 401

    permissions = get_customer_permissions(account["account_id"])

    if not permissions:
        return jsonify({
            "error": "Customer account is inactive or unavailable"
        }), 403

    # Prevent session fixation.
    session.clear()
    session.permanent = True
    session["account"] = account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "success": True,
        "account": account,
        "permissions": sorted(permissions),
    }), 200


@auth_bp.route("/customer/logout", methods=["POST"])
def customer_logout():
    """Clear the current customer session."""
    session.clear()
    return jsonify({"success": True}), 200


@auth_bp.route("/customer/me", methods=["GET"])
def customer_me():
    """Verify the current customer session against the database."""
    account = session.get("account")

    if not isinstance(account, dict):
        return jsonify({"error": "Authentication required"}), 401

    if account.get("role_name") != "customer":
        return jsonify({"error": "Customer authentication required"}), 401

    account_id = account.get("account_id")

    try:
        current_account = get_current_session_customer(account_id)

        if current_account is None:
            session.clear()
            return jsonify({"error": "Authentication required"}), 401

        permissions = get_customer_permissions(account_id)

        if not permissions:
            session.clear()
            return jsonify({"error": "Customer account is inactive"}), 403

    except Exception:
        current_app.logger.exception("Unable to verify customer session")
        return jsonify({"error": "Unable to verify authorization"}), 503

    session["account"] = current_account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "account": current_account,
        "permissions": sorted(permissions),
    }), 200
