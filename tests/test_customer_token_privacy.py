
import uuid
from unittest.mock import patch

from app.utils.auth import hash_password
from app.utils.db import get_cursor


def test_customer_can_only_view_own_token(app):
    tag = uuid.uuid4().hex[:10]
    customer_ids = []
    account_ids = []
    token_ids = []

    try:
        with app.app_context():
            with get_cursor() as cursor:
                for name in ("Privacy Customer A", "Privacy Customer B"):
                    cursor.execute(
                        """
                        INSERT INTO Customer (full_name, phone, email)
                        VALUES (%s, %s, %s)
                        """,
                        (f"{name} {tag}", f"P{tag}", f"{tag}-{len(customer_ids)}@example.invalid"),
                    )
                    customer_id = cursor.lastrowid
                    customer_ids.append(customer_id)

                    username = f"privacy_{tag}_{len(customer_ids)}"
                    cursor.execute(
                        """
                        INSERT INTO customer_account
                            (customer_id, username, password_hash, is_active)
                        VALUES (%s, %s, %s, 1)
                        """,
                        (customer_id, username, hash_password("Privacy-test-Password9")),
                    )
                    account_ids.append(cursor.lastrowid)

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                for index, customer_id in enumerate(customer_ids):
                    cursor.execute(
                        """
                        INSERT INTO Token (queue_id, customer_id, token_number, status)
                        VALUES (1, %s, %s, 'waiting')
                        """,
                        (customer_id, next_number + index),
                    )
                    token_ids.append(cursor.lastrowid)

        with app.test_client() as client_a:
            login = client_a.post(
                "/api/auth/customer/login",
                json={
                    "username": f"privacy_{tag}_1",
                    "password": "Privacy-test-Password9",
                },
            )
            assert login.status_code == 200, login.get_json()

            own_response = client_a.get(f"/api/tokens/{token_ids[0]}")
            other_response = client_a.get(f"/api/tokens/{token_ids[1]}")

            assert own_response.status_code == 200
            assert other_response.status_code in (403, 404)

            if own_response.status_code == 200:
                data = own_response.get_json()
                assert "customer_phone" not in data
                assert "customer_id" not in data
                assert "waiting_ahead" in data
                assert "estimated_wait_minutes" in data

        with app.test_client() as anonymous:
            response = anonymous.get(f"/api/tokens/{token_ids[0]}")
            assert response.status_code in (401, 403)

    finally:
        if token_ids:
            with app.app_context():
                with get_cursor() as cursor:
                    for token_id in token_ids:
                        cursor.execute(
                            "DELETE FROM Notification WHERE token_id = %s",
                            (token_id,),
                        )
                        cursor.execute(
                            "DELETE FROM Queue_History WHERE token_id = %s",
                            (token_id,),
                        )
                    cursor.executemany(
                        "DELETE FROM Token WHERE token_id = %s",
                        [(token_id,) for token_id in token_ids],
                    )

        if customer_ids:
            with app.app_context():
                with get_cursor() as cursor:
                    for customer_id in customer_ids:
                        cursor.execute(
                            "DELETE FROM Notification WHERE customer_id = %s",
                            (customer_id,),
                        )
                    cursor.executemany(
                        "DELETE FROM customer_account WHERE account_id = %s",
                        [(account_id,) for account_id in account_ids],
                    )
                    cursor.executemany(
                        "DELETE FROM Customer WHERE customer_id = %s",
                        [(customer_id,) for customer_id in customer_ids],
                    )


def test_staff_with_queue_operate_can_read_any_token(app):
    """Staff with queue_operate permission can read any token's full details;
    staff without queue_operate receives 403."""
    tag = uuid.uuid4().hex[:10]
    customer_id = None
    token_id = None

    # Staff account with queue_operate (branch 1, counter 1, queue 1)
    staff_account = {
        "account_id": 2,
        "staff_id": 2,
        "username": "test_staff",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    try:
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (f"StaffReadTest {tag}", f"SRT{tag}", f"{tag}@example.invalid"),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                cursor.execute(
                    """
                    INSERT INTO Token (queue_id, customer_id, token_number, status, counter_id, staff_id)
                    VALUES (1, %s, %s, 'waiting', 1, 2)
                    """,
                    (customer_id, next_number),
                )
                token_id = cursor.lastrowid

        # Staff WITH queue_operate -> 200 with full token data
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["account"] = staff_account
                session["permissions"] = ["queue_operate"]

            with patch(
                "app.utils.auth.get_current_session_account",
                return_value=staff_account,
            ), patch(
                "app.utils.auth.get_user_permissions",
                return_value={"queue_operate"},
            ):
                response = client.get(f"/api/tokens/{token_id}")
                assert response.status_code == 200, response.get_json()
                data = response.get_json()

                # Staff sees full token including customer PII and wait estimates
                assert data["customer_id"] == customer_id
                assert "customer_phone" in data
                assert data["customer_phone"] == f"SRT{tag}"
                assert "waiting_ahead" in data
                assert "estimated_wait_minutes" in data
                assert data["status"] == "waiting"
                assert data["queue_id"] == 1

        # Staff WITHOUT queue_operate -> 403
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["account"] = staff_account
                session["permissions"] = ["staff_view"]

            with patch(
                "app.utils.auth.get_current_session_account",
                return_value=staff_account,
            ), patch(
                "app.utils.auth.get_user_permissions",
                return_value={"staff_view"},
            ):
                response = client.get(f"/api/tokens/{token_id}")
                assert response.status_code == 403, response.get_json()
                assert response.get_json()["error"] == "Permission denied"

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM Notification WHERE token_id = %s",
                        (token_id,),
                    )
                    cursor.execute(
                        "DELETE FROM Queue_History WHERE token_id = %s",
                        (token_id,),
                    )
                    cursor.execute(
                        "DELETE FROM Token WHERE token_id = %s",
                        (token_id,),
                    )

        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM Notification WHERE customer_id = %s",
                        (customer_id,),
                    )
                    cursor.execute(
                        "DELETE FROM Customer WHERE customer_id = %s",
                        (customer_id,),
                    )
