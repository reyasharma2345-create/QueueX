
from contextlib import contextmanager

import pytest

from app.utils import procedures
from app.utils.db import get_cursor, get_db


class FailingHistoryCursor:
    """Wrap a real cursor and fail only on Queue_History inserts."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, operation, params=None):
        normalized = " ".join(operation.lower().split())

        if normalized.startswith("insert into queue_history"):
            raise RuntimeError("Simulated Queue_History insert failure")

        if params is None:
            return self._cursor.execute(operation)

        return self._cursor.execute(operation, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ConnectionProxy:
    """Expose a connection while wrapping cursors returned to the procedure."""

    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        return FailingHistoryCursor(
            self._connection.cursor(*args, **kwargs)
        )

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_call_specific_token_rolls_back_when_history_insert_fails(monkeypatch):
    queue_id = 2
    counter_id = 4
    staff_id = 3

    customer_id = None
    token_id = None

    # Confirm the test fixture's counter, staff member, and queue are valid.
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT c.counter_id, c.branch_id, c.queue_id, c.is_active,
                   s.staff_id, s.is_active AS staff_active,
                   s.branch_id AS staff_branch
            FROM Counter c
            JOIN Staff s ON s.staff_id = %s
            WHERE c.counter_id = %s
              AND c.queue_id = %s
            """,
            (staff_id, counter_id, queue_id),
        )
        assignment = cursor.fetchone()

    assert assignment is not None, (
        "Expected test counter 4 to serve queue 2 with staff member 3"
    )
    assert assignment["is_active"] == 1
    assert assignment["staff_active"] == 1
    assert assignment["branch_id"] == assignment["staff_branch"]

    try:
        # Create isolated test data using the normal DB context, before
        # injecting the failure into procedures.get_db.
        with get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO Customer (full_name, phone, email)
                VALUES (%s, %s, %s)
                """,
                (
                    "Rollback Test Customer",
                    "9000000098",
                    "rollback_test_20261019@example.invalid",
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

        # Snapshot history IDs for this token. No history should be added
        # if the procedure rolls back correctly.
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT history_id
                FROM Queue_History
                WHERE token_id = %s
                """,
                (token_id,),
            )
            history_before = {
                row["history_id"] for row in cursor.fetchall()
            }

        @contextmanager
        def failing_get_db():
            # Keep the original context manager responsible for commit,
            # rollback, and closing the underlying connection.
            with get_db() as connection:
                yield ConnectionProxy(connection)

        monkeypatch.setattr(procedures, "get_db", failing_get_db)

        with pytest.raises(
            RuntimeError, match="Simulated Queue_History insert failure"
        ):
            procedures.call_specific_token(
                counter_id, staff_id, token_number
            )

        # The token update occurred before the injected failure, so these
        # assertions prove that the update was rolled back.
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT status, counter_id, staff_id, called_at
                FROM Token
                WHERE token_id = %s
                """,
                (token_id,),
            )
            token = cursor.fetchone()

            cursor.execute(
                """
                SELECT history_id
                FROM Queue_History
                WHERE token_id = %s
                """,
                (token_id,),
            )
            history_after = {
                row["history_id"] for row in cursor.fetchall()
            }

        assert token is not None
        assert token["status"] == "waiting"
        assert token["counter_id"] is None
        assert token["staff_id"] is None
        assert token["called_at"] is None
        assert history_after == history_before

    finally:
        # Remove only records created by this test.
        if token_id is not None:
            with get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM Queue_History WHERE token_id = %s",
                    (token_id,),
                )
                cursor.execute(
                    "DELETE FROM Notification WHERE token_id = %s",
                    (token_id,),
                )
                cursor.execute(
                    "DELETE FROM Token WHERE token_id = %s",
                    (token_id,),
                )

        if customer_id is not None:
            with get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM Customer WHERE customer_id = %s",
                    (customer_id,),
                )
