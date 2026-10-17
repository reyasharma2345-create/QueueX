
from unittest.mock import patch


def test_login_rejects_missing_json(client):
    response = client.post("/api/auth/login")

    assert response.status_code == 400
    assert response.get_json()["error"] == "JSON request body required"


def test_login_rejects_missing_credentials(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "staff1"},
    )

    assert response.status_code == 400


def test_login_rejects_invalid_credentials(client):
    with patch(
        "app.auth_routes.authenticate_user",
        return_value=None,
    ):
        response = client.post(
            "/api/auth/login",
            json={
                "username": "staff1",
                "password": "wrong-password",
            },
        )

    assert response.status_code == 401


def test_me_rejects_unauthenticated_request(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_logout_clears_session(client):
    with client.session_transaction() as session:
        session["account"] = {
            "account_id": 1,
            "staff_id": 1,
            "username": "staff1",
            "full_name": "Test Staff",
            "branch_id": 1,
            "role_id": 2,
            "role_name": "staff",
        }
        session["permissions"] = ["queue_operate"]

    response = client.post("/api/auth/logout")

    assert response.status_code == 200

    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_login_establishes_session(client):
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    permissions = {"queue_operate", "staff_view"}

    with patch(
        "app.auth_routes.authenticate_user",
        return_value=account,
    ), patch(
        "app.auth_routes.get_user_permissions",
        return_value=permissions,
    ):
        response = client.post(
            "/api/auth/login",
            json={
                "username": "staff1",
                "password": "example-password",
            },
        )

    assert response.status_code == 200

    body = response.get_json()
    assert body["success"] is True
    assert body["account"]["username"] == "staff1"
    assert set(body["permissions"]) == permissions

    # /me must verify the current account and permissions against the DB.
    with patch(
        "app.auth_routes.get_current_session_account",
        return_value=account,
    ), patch(
        "app.auth_routes.get_user_permissions",
        return_value=permissions,
    ):
        response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.get_json()["account"]["username"] == "staff1"
    assert set(response.get_json()["permissions"]) == permissions


def test_protected_route_rejects_unauthenticated_request(client):
    response = client.post(
        "/api/counters/1/call-next",
        json={},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "Authentication required"



def test_protected_route_rejects_missing_permission(client):
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "test_staff",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    with client.session_transaction() as session:
        session["account"] = account
        session["permissions"] = ["staff_view"]

    with patch(
        "app.utils.auth.get_current_session_account",
        return_value=account,
    ), patch(
        "app.utils.auth.get_user_permissions",
        return_value={"staff_view"},
    ):
        response = client.post(
            "/api/counters/1/call-next",
            json={},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Permission denied"

def test_me_clears_session_when_account_is_inactive(client):
    with client.session_transaction() as session:
        session["account"] = {
            "account_id": 1,
            "staff_id": 1,
            "username": "staff1",
            "full_name": "Test Staff",
            "branch_id": 1,
            "role_id": 2,
            "role_name": "staff",
        }
        session["permissions"] = ["queue_operate"]

    with patch(
        "app.auth_routes.get_current_session_account",
        return_value=None,
    ):
        response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.get_json()["error"] == "Authentication required"

    with client.session_transaction() as session:
        assert "account" not in session
        assert "permissions" not in session

def test_protected_route_rejects_revoked_permission(client):
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    with client.session_transaction() as session:
        session["account"] = account
        # Simulate a permission that was granted at login.
        session["permissions"] = ["queue_operate"]

    with patch(
        "app.utils.auth.get_current_session_account",
        return_value=account,
    ), patch(
        "app.utils.auth.get_user_permissions",
        return_value={"staff_view"},
    ):
        response = client.post(
            "/api/counters/1/call-next",
            json={},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Permission denied"

    with client.session_transaction() as session:
        assert session["permissions"] == ["staff_view"]

def test_protected_route_fails_closed_when_database_verification_fails(client):
    account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    with client.session_transaction() as session:
        session["account"] = account
        session["permissions"] = ["queue_operate"]

    with patch(
        "app.utils.auth.get_current_session_account",
        side_effect=Exception("Database unavailable"),
    ):
        response = client.post(
            "/api/counters/1/call-next",
            json={},
        )

    assert response.status_code == 503
    assert response.get_json()["error"] == "Unable to verify authorization"
