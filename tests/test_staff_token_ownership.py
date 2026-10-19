
import uuid
from unittest.mock import patch

from app.utils.db import get_cursor


def test_staff_cannot_operate_on_another_staff_members_tokens(app):
    """Wrong staff must not change token state or history."""
    tag = uuid.uuid4().hex[:12]
    customer_id = None
    token_ids = []

    staff_account = {
        "account_id": 1,
        "staff_id": 1,
        "username": "staff1",
        "full_name": "Test Staff One",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    # Each endpoint receives a token in a status it normally accepts.
    operations = [
        ("/serve", "called", {}),
        ("/complete", "serving", {}),
        ("/no-show", "called", {"window_minutes": 0}),
        ("/reissue", "called", {}),
    ]

    try:
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Ownership Test {tag}",
                        f"OWN{tag}",
                        f"{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

                # Do not change Queue.last_token_number. Select unused
                # numbers for these temporary test records only.
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token
                    WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                for index, (endpoint, status, _) in enumerate(operations):
                    cursor.execute(
                        """
                        INSERT INTO Token (
                            queue_id,
                            customer_id,
                            token_number,
                            status,
                            counter_id,
                            staff_id,
                            called_at,
                            served_at
                        )
                        VALUES (
                            1, %s, %s, %s, 1, 2,
                            CURRENT_TIMESTAMP,
                            CASE WHEN %s = 'serving'
                                 THEN CURRENT_TIMESTAMP
                                 ELSE NULL
                            END
                        )
                        """,
                        (
                            customer_id,
                            next_number + index,
                            status,
                            status,
                        ),
                    )
                    token_ids.append(cursor.lastrowid)

        # Record the initial token state and history counts.
        before = {}
        with app.app_context():
            with get_cursor() as cursor:
                for token_id in token_ids:
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
                        SELECT COUNT(*) AS total
                        FROM Queue_History
                        WHERE token_id = %s
                        """,
                        (token_id,),
                    )
                    history_count = cursor.fetchone()["total"]

                    before[token_id] = (token, history_count)

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
                for token_id, (endpoint, _, payload) in zip(
                    token_ids, operations
                ):
                    response = client.post(
                        f"/api/tokens/{token_id}{endpoint}",
                        json=payload,
                    )
                    assert response.status_code == 403, (
                        endpoint,
                        response.status_code,
                        response.get_json(),
                    )

        # Confirm all rejected requests left the database unchanged.
        with app.app_context():
            with get_cursor() as cursor:
                for token_id in token_ids:
                    cursor.execute(
                        """
                        SELECT status, counter_id, staff_id
                        FROM Token
                        WHERE token_id = %s
                        """,
                        (token_id,),
                    )
                    token_after = cursor.fetchone()

                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM Queue_History
                        WHERE token_id = %s
                        """,
                        (token_id,),
                    )
                    history_after = cursor.fetchone()["total"]

                    token_before, history_before = before[token_id]

                    assert token_after == token_before
                    assert history_after == history_before

    finally:
        # Delete only records created by this test.
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

def test_assigned_staff_can_operate_on_own_tokens(app):
    """Assigned staff can access all four token-operation endpoints."""
    import uuid
    from unittest.mock import patch

    from app.utils.db import get_cursor

    tag = uuid.uuid4().hex[:12]
    customer_id = None
    token_ids = []

    account = {
        "account_id": 2,
        "staff_id": 2,
        "username": "test_staff",
        "full_name": "Test Staff",
        "branch_id": 1,
        "role_id": 2,
        "role_name": "staff",
    }

    operations = [
        ("/serve", "called", {}),
        ("/complete", "serving", {}),
        ("/no-show", "called", {"window_minutes": 0}),
        ("/reissue", "called", {}),
    ]

    try:
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name, phone, email)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        f"Authorized Staff Test {tag}",
                        f"AUTH{tag}",
                        f"{tag}@example.invalid",
                    ),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token
                    WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                for index, (_, status, _) in enumerate(operations):
                    cursor.execute(
                        """
                        INSERT INTO Token (
                            queue_id, customer_id, token_number,
                            status, counter_id, staff_id,
                            called_at, served_at
                        )
                        VALUES (
                            1, %s, %s, %s, 1, 2,
                            CURRENT_TIMESTAMP,
                            CASE WHEN %s = 'serving'
                                 THEN CURRENT_TIMESTAMP
                                 ELSE NULL
                            END
                        )
                        """,
                        (
                            customer_id,
                            next_number + index,
                            status,
                            status,
                        ),
                    )
                    token_ids.append(cursor.lastrowid)

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["account"] = account
                session["permissions"] = ["queue_operate"]

            with (
                patch(
                    "app.utils.auth.get_current_session_account",
                    return_value=account,
                ),
                patch(
                    "app.utils.auth.get_user_permissions",
                    return_value={"queue_operate"},
                ),
                patch("app.routes.serve_token") as serve_mock,
                patch("app.routes.complete_token") as complete_mock,
                patch("app.routes.mark_no_show", return_value=True) as no_show_mock,
                patch(
                    "app.utils.procedures.reissue_token",
                    return_value=True,
                ) as reissue_mock,
                patch("app.routes.send_token_serving_notification"),
                patch("app.routes.send_token_completed_notification"),
            ):
                for token_id, (endpoint, _, payload) in zip(
                    token_ids, operations
                ):
                    response = client.post(
                        f"/api/tokens/{token_id}{endpoint}",
                        json=payload,
                    )
                    assert response.status_code == 200, (
                        endpoint,
                        response.status_code,
                        response.get_json(),
                    )
                    assert response.get_json()["success"] is True

                serve_mock.assert_called_once_with(token_ids[0])
                complete_mock.assert_called_once_with(token_ids[1])
                no_show_mock.assert_called_once_with(token_ids[2], 0)
                reissue_mock.assert_called_once_with(token_ids[3])

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
