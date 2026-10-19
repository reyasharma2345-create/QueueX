import uuid
from unittest.mock import patch

from app.utils.db import get_cursor


def test_customer_request_to_staff_completion_e2e(app, required_document_seed):
    """Exercise customer request creation through staff token completion."""

    queue_id = 1
    counter_id = 1
    staff_id = 1
    tag = uuid.uuid4().hex[:10]

    request_id = None
    token_id = None
    token_number = None
    customer_id = None
    customer_account_id = None

    staff_account = {
        "account_id": 1,
        "staff_id": staff_id,
        "username": "lifecycle_test_staff",
        "full_name": "Lifecycle Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    try:
        # Verify the test database has a valid queue, counter, and staff member.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        q.is_active AS queue_active,
                        c.is_active AS counter_active,
                        c.queue_id AS counter_queue_id,
                        s.is_active AS staff_active,
                        s.branch_id AS staff_branch_id,
                        q.branch_id AS queue_branch_id
                    FROM Queue q
                    JOIN Counter c ON c.counter_id = %s
                    JOIN Staff s ON s.staff_id = %s
                    WHERE q.queue_id = %s
                    """,
                    (counter_id, staff_id, queue_id),
                )
                setup = cursor.fetchone()

                assert setup is not None, "Queue/counter/staff setup missing"
                assert setup["queue_active"] == 1
                assert setup["counter_active"] == 1
                assert setup["staff_active"] == 1
                assert setup["counter_queue_id"] == queue_id
                assert setup["staff_branch_id"] == setup["queue_branch_id"]

                # Create a unique test customer.
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"E2E Customer {tag}",
                        f"E2E{tag}",
                        f"e2e-{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

                from app.utils.auth import hash_password

                username = f"e2e_customer_{tag}"
                password = f"E2E-password-{tag}-X9"

                cursor.execute(
                    """
                    INSERT INTO customer_account
                        (customer_id, username, password_hash, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (customer_id, username, hash_password(password)),
                )
                customer_account_id = cursor.lastrowid

        # Customer creates a request, marks documents ready, and obtains a token.
        with app.test_client() as customer_client:
            login = customer_client.post(
                "/api/auth/customer/login",
                json={"username": username, "password": password},
            )
            assert login.status_code == 200, login.get_json()

            created = customer_client.post(
                "/api/customer-requests",
                json={"queue_id": queue_id},
            )
            assert created.status_code == 201, created.get_json()

            request_id = created.get_json()["request_id"]
            assert created.get_json()["request_status"] == "draft"

            details = customer_client.get(
                f"/api/customer-requests/{request_id}"
            )
            assert details.status_code == 200, details.get_json()

            documents = details.get_json()["documents"]
            assert documents, "The request should have configured documents"

            for document in documents:
                updated = customer_client.patch(
                    f"/api/customer-requests/{request_id}/documents",
                    json={
                        "document_id": document["document_id"],
                        "readiness_status": "available",
                    },
                )
                assert updated.status_code == 200, updated.get_json()

            confirmed = customer_client.post(
                f"/api/customer-requests/{request_id}/confirm-readiness"
            )
            assert confirmed.status_code == 200, confirmed.get_json()
            assert confirmed.get_json()["request_status"] == "ready"
            assert confirmed.get_json()["readiness_confirmed"] is True

            queued = customer_client.post(
                f"/api/customer-requests/{request_id}/queue"
            )
            assert queued.status_code == 200, queued.get_json()

            queued_data = queued.get_json()
            token_id = queued_data["token_id"]
            token_number = queued_data.get("token_number")

            assert token_id is not None
            assert queued_data["request_status"] == "queued"
            assert queued_data["queue_id"] == queue_id

            # Retrieve the exact token number if the API omits it.
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            cr.customer_id,
                            cr.queue_id,
                            cr.request_status,
                            cr.readiness_confirmed,
                            cr.token_id,
                            t.customer_id AS token_customer_id,
                            t.queue_id AS token_queue_id,
                            t.token_number,
                            t.status AS token_status
                        FROM customer_request cr
                        JOIN Token t ON t.token_id = cr.token_id
                        WHERE cr.request_id = %s
                        """,
                        (request_id,),
                    )
                    linked = cursor.fetchone()

                    assert linked is not None
                    assert linked["customer_id"] == customer_id
                    assert linked["token_customer_id"] == customer_id
                    assert linked["queue_id"] == queue_id
                    assert linked["token_queue_id"] == queue_id
                    assert linked["request_status"] == "queued"
                    assert linked["readiness_confirmed"] in (1, True)
                    assert linked["token_id"] == token_id
                    assert linked["token_status"] == "waiting"

                    db_token_number = linked["token_number"]
                    assert db_token_number is not None

                    if token_number is not None:
                        assert int(token_number) == int(db_token_number)

                    token_number = db_token_number

        # Staff processes only the token created by this test.
        with app.test_client() as staff_client:
            with staff_client.session_transaction() as session:
                session["account"] = staff_account
                session["permissions"] = ["queue_operate"]

            with (
                patch(
                    "app.utils.auth.get_current_session_account",
                    return_value=staff_account,
                ),
                patch(
                    "app.utils.auth.get_user_permissions",
                    return_value={"queue_operate"},
                ),
                patch("app.routes.send_token_called_notification"),
                patch("app.routes.send_token_serving_notification"),
                patch("app.routes.send_token_completed_notification"),
            ):
                # Verify the exact token is still waiting immediately before
                # asking the staff endpoint to call it.
                with app.app_context():
                    with get_cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT token_id, queue_id, token_number, status
                            FROM Token
                            WHERE token_id = %s
                            """,
                            (token_id,),
                        )
                        token_before_call = cursor.fetchone()

                        assert token_before_call is not None
                        assert token_before_call["queue_id"] == queue_id
                        assert int(token_before_call["token_number"]) == int(
                            token_number
                        )
                        assert token_before_call["status"] == "waiting"

                called = staff_client.post(
                    f"/api/counters/{counter_id}/call-specific",
                    json={"token_number": int(token_number)},
                )
                assert called.status_code == 200, (
                    called.status_code,
                    called.get_json(),
                )

                with app.app_context():
                    with get_cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT status, counter_id, staff_id, queue_id
                            FROM Token
                            WHERE token_id = %s
                            """,
                            (token_id,),
                        )
                        token = cursor.fetchone()

                        assert token is not None
                        assert token["status"] == "called"
                        assert token["counter_id"] == counter_id
                        assert token["staff_id"] == staff_id
                        assert token["queue_id"] == queue_id

                served = staff_client.post(
                    f"/api/tokens/{token_id}/serve"
                )
                assert served.status_code == 200, (
                    served.status_code,
                    served.get_json(),
                )

                completed = staff_client.post(
                    f"/api/tokens/{token_id}/complete"
                )
                assert completed.status_code == 200, (
                    completed.status_code,
                    completed.get_json(),
                )

        # Verify the final request/token relationship and status history.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        cr.customer_id,
                        cr.queue_id,
                        cr.request_status,
                        cr.readiness_confirmed,
                        cr.token_id,
                        t.customer_id AS token_customer_id,
                        t.queue_id AS token_queue_id,
                        t.token_number,
                        t.status AS token_status,
                        t.counter_id,
                        t.staff_id
                    FROM customer_request cr
                    JOIN Token t ON t.token_id = cr.token_id
                    WHERE cr.request_id = %s
                    """,
                    (request_id,),
                )
                final_state = cursor.fetchone()

                assert final_state is not None
                assert final_state["customer_id"] == customer_id
                assert final_state["token_customer_id"] == customer_id
                assert final_state["queue_id"] == queue_id
                assert final_state["token_queue_id"] == queue_id
                assert final_state["token_id"] == token_id
                assert final_state["request_status"] == "queued"
                assert final_state["readiness_confirmed"] in (1, True)
                assert final_state["token_status"] == "completed"
                assert final_state["counter_id"] == counter_id
                assert final_state["staff_id"] == staff_id
                assert int(final_state["token_number"]) == int(token_number)

                cursor.execute(
                    """
                    SELECT previous_status, new_status, changed_by
                    FROM Queue_History
                    WHERE token_id = %s
                    ORDER BY history_id
                    """,
                    (token_id,),
                )
                history = cursor.fetchall()

                assert [
    (
        row["previous_status"],
        row["new_status"],
        row["changed_by"],
    )
    for row in history
] == [
    # Initial token creation.
    (None, "waiting", None),
    # Staff lifecycle transitions.
    ("waiting", "called", staff_id),
    ("called", "serving", staff_id),
    ("serving", "completed", staff_id),
]

    finally:
        # Delete only the request, token, and customer created by this test.
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT token_id
                        FROM customer_request
                        WHERE request_id = %s
                        """,
                        (request_id,),
                    )
                    row = cursor.fetchone()
                    linked_token_id = row["token_id"] if row else token_id

                    if linked_token_id is not None:
                        cursor.execute(
                            "DELETE FROM Notification WHERE token_id = %s",
                            (linked_token_id,),
                        )
                        cursor.execute(
                            "DELETE FROM Queue_History WHERE token_id = %s",
                            (linked_token_id,),
                        )

                    cursor.execute(
                        "DELETE FROM request_document WHERE request_id = %s",
                        (request_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE request_id = %s",
                        (request_id,),
                    )

                    if linked_token_id is not None:
                        cursor.execute(
                            "DELETE FROM Token WHERE token_id = %s",
                            (linked_token_id,),
                        )

        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM Notification WHERE customer_id = %s",
                        (customer_id,),
                    )

                    if customer_account_id is not None:
                        cursor.execute(
                            "DELETE FROM customer_account WHERE account_id = %s",
                            (customer_account_id,),
                        )

                    cursor.execute(
                        "DELETE FROM Customer WHERE customer_id = %s",
                        (customer_id,),
                    )
