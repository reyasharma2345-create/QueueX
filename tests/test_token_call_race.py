import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import uuid

from app.utils.db import get_cursor


def test_simultaneous_calls_only_call_token_once(app):
    """Two concurrent calls must not create duplicate call history."""
    tag = uuid.uuid4().hex[:12]
    customer_id = None
    token_id = None
    barrier = threading.Barrier(2)

    try:
        # Create a unique customer and waiting token.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Concurrent Call Test {tag}",
                        f"CC{tag}",
                        f"{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    SELECT last_token_number
                    FROM Queue
                    WHERE queue_id = 1 AND is_active = 1
                    FOR UPDATE
                    """
                )
                queue = cursor.fetchone()
                assert queue is not None

                token_number = queue["last_token_number"] + 1

                cursor.execute(
                    """
                    UPDATE Queue
                    SET last_token_number = %s
                    WHERE queue_id = 1
                    """,
                    (token_number,),
                )

                cursor.execute(
                    """
                    INSERT INTO Token (queue_id, customer_id, token_number)
                    VALUES (1, %s, %s)
                    """,
                    (customer_id, token_number),
                )
                token_id = cursor.lastrowid

        def call_token():
            with app.test_client() as test_client:
                barrier.wait(timeout=10)
                return test_client.post(
                    f"/api/tokens/{token_id}/call",
                    json={"counter_id": 1, "staff_id": 2},
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(call_token),
                executor.submit(call_token),
            ]
            responses = [future.result(timeout=30) for future in futures]

        # Exactly one call should succeed; the other must be rejected.
        assert sorted(response.status_code for response in responses) == [
            200, 400
        ], [
            (response.status_code, response.get_json())
            for response in responses
        ]

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, counter_id, staff_id
                    FROM Token
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                token = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT COUNT(*) AS history_count
                    FROM Queue_History
                    WHERE token_id = %s
                      AND previous_status = 'waiting'
                      AND new_status = 'called'
                    """,
                    (token_id,),
                )
                history_count = cursor.fetchone()["history_count"]

        assert token["status"] == "called"
        assert token["counter_id"] == 1
        assert token["staff_id"] == 2
        assert history_count == 1

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
