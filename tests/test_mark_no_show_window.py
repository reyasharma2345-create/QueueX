import uuid

from app.utils.db import get_cursor
from app.utils.procedures import mark_no_show


def test_mark_no_show_rejects_token_before_window_elapsed(app):
    """An early no-show attempt must leave the token and history unchanged."""
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
                        f"No Show Window {tag}",
                        f"NSW{tag}",
                        f"no-show-window-{tag}@example.invalid",
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
                        (queue_id, customer_id, token_number, status, called_at)
                    VALUES (%s, %s, %s, 'called', CURRENT_TIMESTAMP)
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

            # The five-minute no-show window has not elapsed.
            assert mark_no_show(token_id, 5) is False

            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM Token WHERE token_id = %s",
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
            assert token["status"] == "called"
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
