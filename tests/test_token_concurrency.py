
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.utils.db import get_cursor


def test_concurrent_token_requests_receive_unique_numbers(app):
    """Concurrent token requests must receive distinct numbers."""

    queue_id = 1
    test_tag = uuid.uuid4().hex[:12]
    customer_ids = []
    barrier = threading.Barrier(2)

    def request_token(customer_id):
        with app.test_client() as client:
            barrier.wait(timeout=10)
            return client.post(
                "/api/tokens",
                json={
                    "queue_id": queue_id,
                    "customer_id": customer_id,
                },
            )

    try:
        # Create and track isolated customers inside the protected block.
        with app.app_context():
            with get_cursor() as cursor:
                for number in range(2):
                    cursor.execute(
                        """
                        INSERT INTO Customer (full_name, phone, email)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            f"Concurrency Test {test_tag} {number}",
                            f"CT{test_tag[:10]}{number}",
                            f"{test_tag}{number}@example.invalid",
                        ),
                    )
                    customer_ids.append(cursor.lastrowid)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(request_token, customer_id)
                for customer_id in customer_ids
            ]
            responses = [
                future.result(timeout=30) for future in futures
            ]

        for response in responses:
            assert response.status_code == 201, (
                response.status_code,
                response.get_json(),
            )

        results = [response.get_json() for response in responses]

        assert all(result and result.get("token_id") for result in results)
        assert all(result.get("token_number") for result in results)

        token_ids = [result["token_id"] for result in results]
        token_numbers = [result["token_number"] for result in results]

        assert len(set(token_ids)) == 2, token_ids
        assert len(set(token_numbers)) == 2, token_numbers

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT token_id, customer_id, queue_id, token_number
                    FROM Token
                    WHERE token_id IN (%s, %s)
                    ORDER BY token_id
                    """,
                    tuple(token_ids),
                )
                stored_tokens = cursor.fetchall()

        assert len(stored_tokens) == 2, stored_tokens
        assert {row["customer_id"] for row in stored_tokens} == set(customer_ids)
        assert all(row["queue_id"] == queue_id for row in stored_tokens)
        assert len({row["token_number"] for row in stored_tokens}) == 2

    finally:
        # Clean up only records associated with customers created by this test.
        if customer_ids:
            with app.app_context():
                with get_cursor() as cursor:
                    placeholders = ", ".join(["%s"] * len(customer_ids))
                    customer_params = tuple(customer_ids)

                    cursor.execute(
                        f"""
                        SELECT token_id
                        FROM Token
                        WHERE customer_id IN ({placeholders})
                        """,
                        customer_params,
                    )
                    token_ids_to_delete = [
                        row["token_id"] for row in cursor.fetchall()
                    ]

                    if token_ids_to_delete:
                        token_placeholders = ", ".join(
                            ["%s"] * len(token_ids_to_delete)
                        )
                        token_params = tuple(token_ids_to_delete)

                        cursor.execute(
                            f"""
                            DELETE FROM Queue_History
                            WHERE token_id IN ({token_placeholders})
                            """,
                            token_params,
                        )
                        cursor.execute(
                            f"""
                            DELETE FROM Notification
                            WHERE token_id IN ({token_placeholders})
                               OR customer_id IN ({placeholders})
                            """,
                            token_params + customer_params,
                        )
                        cursor.execute(
                            f"""
                            DELETE FROM Token
                            WHERE token_id IN ({token_placeholders})
                            """,
                            token_params,
                        )
                    else:
                        cursor.execute(
                            f"""
                            DELETE FROM Notification
                            WHERE customer_id IN ({placeholders})
                            """,
                            customer_params,
                        )

                    cursor.execute(
                        f"""
                        DELETE FROM Customer
                        WHERE customer_id IN ({placeholders})
                        """,
                        customer_params,
                    )
