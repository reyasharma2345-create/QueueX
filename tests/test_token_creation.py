
import uuid

from app.utils.db import get_cursor


def test_normal_token_endpoint_issues_token(client, app):
    """A valid customer and queue should receive a new token."""

    tag = uuid.uuid4().hex[:12]
    customer_id = None
    token_id = None

    try:
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Token Creation Test {tag}",
                        f"TC{tag}",
                        f"{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

        response = client.post(
            "/api/tokens",
            json={"queue_id": 1, "customer_id": customer_id},
        )

        assert response.status_code == 201, response.get_json()

        result = response.get_json()
        assert result is not None
        assert result.get("token_id")
        assert result.get("token_number")

        token_id = result["token_id"]

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT queue_id, customer_id, token_number, status
                    FROM Token
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                token = cursor.fetchone()

        assert token is not None
        assert token["queue_id"] == 1
        assert token["customer_id"] == customer_id
        assert token["token_number"] == result["token_number"]
        assert token["status"] == "waiting"

    finally:
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT token_id
                        FROM Token
                        WHERE customer_id = %s
                        """,
                        (customer_id,),
                    )
                    created_token_ids = [
                        row["token_id"] for row in cursor.fetchall()
                    ]

                    if created_token_ids:
                        placeholders = ", ".join(
                            ["%s"] * len(created_token_ids)
                        )
                        params = tuple(created_token_ids)

                        cursor.execute(
                            f"""
                            DELETE FROM Notification
                            WHERE token_id IN ({placeholders})
                            """,
                            params,
                        )
                        cursor.execute(
                            f"""
                            DELETE FROM Queue_History
                            WHERE token_id IN ({placeholders})
                            """,
                            params,
                        )
                        cursor.execute(
                            f"""
                            DELETE FROM Token
                            WHERE token_id IN ({placeholders})
                            """,
                            params,
                        )

                    cursor.execute(
                        "DELETE FROM Notification WHERE customer_id = %s",
                        (customer_id,),
                    )
                    cursor.execute(
                        "DELETE FROM Customer WHERE customer_id = %s",
                        (customer_id,),
                    )
