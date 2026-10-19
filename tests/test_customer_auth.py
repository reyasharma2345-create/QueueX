from unittest.mock import patch
import uuid

from app.utils.db import get_cursor


def test_customer_login_rejects_missing_json(client):
    response = client.post("/api/auth/customer/login")

    assert response.status_code == 400
    assert response.get_json()["error"] == "JSON request body required"


def test_customer_login_rejects_invalid_credentials(client):
    with patch(
        "app.auth_routes.authenticate_customer",
        return_value=None,
    ):
        response = client.post(
            "/api/auth/customer/login",
            json={"username": "unknown-user", "password": "wrong-password"},
        )

    assert response.status_code == 401


def test_customer_login_establishes_session(client, test_customer):
    response = client.post(
        "/api/auth/customer/login",
        json=test_customer,
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["account"]["username"] == test_customer["username"]

    response = client.get("/api/auth/customer/me")
    assert response.status_code == 200
    assert response.get_json()["account"]["username"] == test_customer["username"]


def test_customer_logout_clears_session(client, test_customer):
    response = client.post(
        "/api/auth/customer/login",
        json=test_customer,
    )
    assert response.status_code == 200

    response = client.post("/api/auth/customer/logout")
    assert response.status_code == 200

    response = client.get("/api/auth/customer/me")
    assert response.status_code == 401


def test_customer_registration_rejects_missing_fields(client):
    response = client.post(
        "/api/auth/customer/register",
        json={"username": "new-user"},
    )

    assert response.status_code == 400


def test_customer_registration_rejects_short_password(client):
    response = client.post(
        "/api/auth/customer/register",
        json={
            "full_name": "Test Customer",
            "username": f"shortpw_{uuid.uuid4().hex[:10]}",
            "password": "123",
        },
    )

    assert response.status_code == 400


def test_customer_registration_rejects_duplicate_username(client, test_customer):
    response = client.post(
        "/api/auth/customer/register",
        json={
            "full_name": "Duplicate Customer",
            "username": test_customer["username"],
            "password": "StrongPassword123!",
        },
    )

    assert response.status_code == 409
    assert "already registered" in response.get_json()["error"]


def test_customer_registration_creates_account(client):
    from app.utils.auth import hash_password

    username = f"reg_{uuid.uuid4().hex[:12]}"
    customer_id = None
    account_id = None

    try:
        response = client.post(
            "/api/auth/customer/register",
            json={
                "full_name": "Registration Test",
                "username": username,
                "password": "StrongPassword123!",
                "email": f"{username}@example.invalid",
                "phone": f"REG{uuid.uuid4().hex[:10]}",
            },
        )

        assert response.status_code == 201
        body = response.get_json()
        assert body["success"] is True
        assert body["account"]["username"] == username

        with client.application.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ca.account_id, ca.customer_id, ca.password_hash
                    FROM customer_account AS ca
                    WHERE ca.username = %s
                    """,
                    (username,),
                )
                row = cursor.fetchone()

                assert row is not None
                account_id = row["account_id"]
                customer_id = row["customer_id"]
                assert row["password_hash"] != "StrongPassword123!"

    finally:
        if account_id is not None:
            with client.application.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM customer_account WHERE account_id = %s",
                        (account_id,),
                    )

        if customer_id is not None:
            with client.application.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM Customer WHERE customer_id = %s",
                        (customer_id,),
                    )
