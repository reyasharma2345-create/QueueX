
import uuid

import pytest

from app.utils.db import get_cursor
from app.utils.procedures import complete_token, serve_token


@pytest.mark.parametrize(
    ("procedure", "procedure_name"),
    [
        (serve_token, "serve_token"),
        (complete_token, "complete_token"),
    ],
)
def test_staff_procedure_rejects_waiting_token(
    app, procedure, procedure_name
):
    """Invalid status transitions must not change token or history."""
    queue_id = 2
    customer_id = None
    token_id = None
    tag = uuid.uuid4().hex[:10]

    try:
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Invalid Transition {tag}",
                        f"INV{tag}",
                        f"invalid-transition-{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

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
                    INSERT INTO Token
                        (queue_id, customer_id, token_number, status)
                    VALUES (%s, %s, %s, 'waiting')
                    """,
                    (queue_id, customer_id, token_number),
                )
                token_id = cursor.lastrowid

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM Queue_History
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                history_before = cursor.fetchone()["total"]

            # The stored procedure should reject a waiting token.
            with pytest.raises(Exception):
                procedure(token_id)

            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, served_at, completed_at
                    FROM Token
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                token = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM Queue_History
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                history_after = cursor.fetchone()["total"]

            assert token is not None
            assert token["status"] == "waiting"
            assert token["served_at"] is None
            assert token["completed_at"] is None
            assert history_after == history_before

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
