
"""Authentication routes for QueueX."""

from flask import Blueprint, jsonify, request, session

from app.utils.auth import (
    authenticate_user,
    get_current_session_account,
    get_user_permissions,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


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

    # A valid role with no permissions must not establish a usable session.
    if not permissions:
        return jsonify({"error": "Account has no assigned permissions"}), 403

    # Prevent session fixation by discarding any existing session contents.
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

@auth_bp.route("/me", methods=["GET"])
def me():
    """Return the current account only after verifying it against the database."""
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
        return jsonify({"error": "Unable to verify authorization"}), 503

    session["account"] = current_account
    session["permissions"] = sorted(permissions)

    return jsonify({
        "account": current_account,
        "permissions": sorted(permissions),
    }), 200
