import uuid

from app.utils.db import get_cursor


def test_staff_queue_lifecycle_uses_real_procedures(app):
    """Verify waiting -> called -> serving -> completed and queue history."""
    queue_id = 2
    counter_id = 4
    staff_id = 3
    tag = uuid.uuid4().hex[:12]

    customer_id = None
    token_id = None

    staff_account = {
        "account_id": staff_id,
        "staff_id": staff_id,
        "username": "lifecycle_test_staff",
        "full_name": "Lifecycle Test Staff",
        "branch_id": 2,
        "role_id": 2,
        "role_name": "staff",
    }

    try:
        with app.app_context():
            with get_cursor() as cursor:
                # Fail safely if this queue is no longer isolated.
                cursor.execute(
                    """
                    SELECT COUNT(*) AS waiting_count
                    FROM Token
                    WHERE queue_id = %s AND status = 'waiting'
                    """,
                    (queue_id,),
                )
                assert cursor.fetchone()["waiting_count"] == 0, (
                    "Queue 2 has waiting tokens; refusing to call an "
                    "unrelated token."
                )

                cursor.execute(
                    """
                    SELECT q.is_active AS queue_active,
                           c.is_active AS counter_active,
                           c.queue_id,
                           s.is_active AS staff_active,
                           s.branch_id AS staff_branch
                    FROM Queue q
                    JOIN Counter c ON c.counter_id = %s
                    JOIN Staff s ON s.staff_id = %s
                    WHERE q.queue_id = %s
                      AND c.branch_id = q.branch_id
                      AND s.branch_id = q.branch_id
                    """,
                    (counter_id, staff_id, queue_id),
                )
                setup = cursor.fetchone()

                assert setup is not None, "Queue, counter, or staff mismatch."
                assert setup["queue_active"] == 1
                assert setup["counter_active"] == 1
                assert setup["staff_active"] == 1
                assert setup["queue_id"] == queue_id

                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Lifecycle Test {tag}",
                        f"LIFE{tag}",
                        f"lifecycle-{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

                # Choose an unused token number without changing the
                # existing queue counter.
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) + 1 AS next_number
                    FROM Token
                    WHERE queue_id = %s
                    """,
                    (queue_id,),
                )
                token_number = cursor.fetchone()["next_number"]

                cursor.execute(
                    """
                    INSERT INTO Token (
                        queue_id, customer_id, token_number, status
                    )
                    VALUES (%s, %s, %s, 'waiting')
                    """,
                    (queue_id, customer_id, token_number),
                )
                token_id = cursor.lastrowid

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["account"] = staff_account
                session["permissions"] = ["queue_operate"]

            # Keep authorization checks realistic while avoiding external
            # notification side effects. The database procedures remain real.
            from unittest.mock import patch

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
                response = client.post(
                    f"/api/counters/{counter_id}/call-next"
                )
                assert response.status_code == 200, (
                    response.status_code,
                    response.get_json(),
                )

                with app.app_context():
                    with get_cursor() as cursor:
                        cursor.execute(
                            "SELECT status, counter_id, staff_id "
                            "FROM Token WHERE token_id = %s",
                            (token_id,),
                        )
                        row = cursor.fetchone()
                        assert row["status"] == "called"
                        assert row["counter_id"] == counter_id
                        assert row["staff_id"] == staff_id

                response = client.post(f"/api/tokens/{token_id}/serve")
                assert response.status_code == 200, (
                    response.status_code,
                    response.get_json(),
                )

                with app.app_context():
                    with get_cursor() as cursor:
                        cursor.execute(
                            "SELECT status FROM Token WHERE token_id = %s",
                            (token_id,),
                        )
                        assert cursor.fetchone()["status"] == "serving"

                response = client.post(
                    f"/api/tokens/{token_id}/complete"
                )
                assert response.status_code == 200, (
                    response.status_code,
                    response.get_json(),
                )

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT status, counter_id, staff_id "
                    "FROM Token WHERE token_id = %s",
                    (token_id,),
                )
                final_token = cursor.fetchone()
                assert final_token["status"] == "completed"
                assert final_token["counter_id"] == counter_id
                assert final_token["staff_id"] == staff_id

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
                    (row["previous_status"], row["new_status"], row["changed_by"])
                    for row in history
                ] == [
                    ("waiting", "called", staff_id),
                    ("called", "serving", staff_id),
                    ("serving", "completed", staff_id),
                ]

    finally:
        # Remove only this test's records, in foreign-key-safe order.
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
