import uuid
import json
from unittest.mock import patch

from app.utils.auth import hash_password
from app.utils.db import get_cursor


def _collect_sse_events(response, max_events=5, timeout=2.0):
    """Collect SSE events from a streaming response."""
    events = []
    buffer = b""
    for chunk in response.response:
        buffer += chunk
        while b"\n\n" in buffer:
            event_data, buffer = buffer.split(b"\n\n", 1)
            events.append(event_data.decode("utf-8"))
            if len(events) >= max_events:
                return events
    return events


def test_sse_customer_receives_initial_event_with_correct_fields(app):
    """Authorized customer receives an initial SSE event with correct content type,
    cache headers, and only status, waiting_ahead, estimated_wait_minutes."""
    tag = uuid.uuid4().hex[:10]
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
                    (f"SSE Customer {tag}", f"SSE{tag}", f"{tag}@example.invalid"),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO customer_account
                        (customer_id, username, password_hash, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (customer_id, f"sse_cust_{tag}", hash_password("SSE-test-Password9")),
                )
                account_id = cursor.lastrowid

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                cursor.execute(
                    """
                    INSERT INTO Token (queue_id, customer_id, token_number, status)
                    VALUES (1, %s, %s, 'waiting')
                    """,
                    (customer_id, next_number),
                )
                token_id = cursor.lastrowid

        with app.test_client() as client:
            login = client.post(
                "/api/auth/customer/login",
                json={"username": f"sse_cust_{tag}", "password": "SSE-test-Password9"},
            )
            assert login.status_code == 200, login.get_json()

            response = client.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 200
            assert response.mimetype == "text/event-stream"
            assert response.headers.get("Cache-Control") == "no-cache"
            assert response.headers.get("X-Accel-Buffering") == "no"

            events = _collect_sse_events(response, max_events=2)
            assert len(events) >= 1, "Expected at least one SSE event"

            first_event = events[0]
            assert first_event.startswith("data: "), f"Expected data event, got: {first_event}"
            data = json.loads(first_event[6:])

            # Only customer-visible fields
            assert set(data.keys()) == {"status", "waiting_ahead", "estimated_wait_minutes"}
            assert data["status"] == "waiting"
            assert isinstance(data["waiting_ahead"], int)
            assert isinstance(data["estimated_wait_minutes"], int)

            # No PII fields
            assert "customer_id" not in data
            assert "customer_phone" not in data
            assert "counter_name" not in data
            assert "staff_name" not in data

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM customer_account WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM Customer WHERE customer_id = %s", (customer_id,))


def test_sse_customer_cannot_stream_other_customer_token(app):
    """Customer cannot stream another customer's token; receives same non-leaking 404."""
    tag = uuid.uuid4().hex[:10]
    customer_ids = []
    account_ids = []
    token_ids = []

    try:
        with app.app_context():
            with get_cursor() as cursor:
                for name in ("SSE Customer A", "SSE Customer B"):
                    cursor.execute(
                        """
                        INSERT INTO Customer (full_name, phone, email)
                        VALUES (%s, %s, %s)
                        """,
                        (f"{name} {tag}", f"SS{tag}{len(customer_ids)}", f"{tag}{len(customer_ids)}@example.invalid"),
                    )
                    customer_id = cursor.lastrowid
                    customer_ids.append(customer_id)

                    username = f"sse_privacy_{tag}_{len(customer_ids)}"
                    cursor.execute(
                        """
                        INSERT INTO customer_account
                            (customer_id, username, password_hash, is_active)
                        VALUES (%s, %s, %s, 1)
                        """,
                        (customer_id, username, hash_password("SSE-test-Password9")),
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
                json={"username": f"sse_privacy_{tag}_1", "password": "SSE-test-Password9"},
            )
            assert login.status_code == 200, login.get_json()

            # Own token -> 200
            own_response = client_a.get(f"/api/tokens/{token_ids[0]}/stream")
            assert own_response.status_code == 200

            # Other customer's token -> 404 (not 403, no existence leak)
            other_response = client_a.get(f"/api/tokens/{token_ids[1]}/stream")
            assert other_response.status_code == 404

    finally:
        if token_ids:
            with app.app_context():
                with get_cursor() as cursor:
                    for token_id in token_ids:
                        cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                        cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.executemany("DELETE FROM Token WHERE token_id = %s", [(tid,) for tid in token_ids])
        if customer_ids:
            with app.app_context():
                with get_cursor() as cursor:
                    for customer_id in customer_ids:
                        cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.executemany("DELETE FROM customer_account WHERE account_id = %s", [(aid,) for aid in account_ids])
                    cursor.executemany("DELETE FROM Customer WHERE customer_id = %s", [(cid,) for cid in customer_ids])


def test_sse_anonymous_or_invalid_session_denied(app):
    """Anonymous or invalid sessions receive 401."""
    tag = uuid.uuid4().hex[:10]
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
                    (f"SSE Anon {tag}", f"SAN{tag}", f"{tag}@example.invalid"),
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
                    INSERT INTO Token (queue_id, customer_id, token_number, status)
                    VALUES (1, %s, %s, 'waiting')
                    """,
                    (customer_id, next_number),
                )
                token_id = cursor.lastrowid

        # Anonymous
        with app.test_client() as anon:
            response = anon.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 401

        # Invalid session
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["account"] = {"role_name": "customer", "account_id": 99999}
                session["permissions"] = []

            response = client.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 401

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM Customer WHERE customer_id = %s", (customer_id,))


def test_sse_staff_queue_operate_access_and_denial(app):
    """Staff with queue_operate can stream; staff without receives 403."""
    tag = uuid.uuid4().hex[:10]
    customer_id = None
    token_id = None

    staff_account = {
        "account_id": 3,
        "staff_id": 3,
        "username": "sse_staff",
        "full_name": "SSE Staff",
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
                    (f"SSE StaffTest {tag}", f"SSS{tag}", f"{tag}@example.invalid"),
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
        client1 = app.test_client()
        with client1.session_transaction() as session:
            session["account"] = staff_account
            session["permissions"] = ["queue_operate"]

        with patch("app.utils.auth.get_current_session_account", return_value=staff_account), \
             patch("app.utils.auth.get_user_permissions", return_value={"queue_operate"}):
            response = client1.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 200, response.get_json()
            assert response.mimetype == "text/event-stream"

            events = _collect_sse_events(response, max_events=1)
            assert len(events) >= 1
            data = json.loads(events[0][6:])  # strip "data: "

            # Staff sees full token including PII
            assert "customer_id" in data
            assert "customer_phone" in data
            assert "waiting_ahead" in data
            assert "estimated_wait_minutes" in data
            assert data["status"] == "waiting"

        # Explicitly close the response to clean up request context
        response.close()

        # Staff WITHOUT queue_operate -> 403
        client2 = app.test_client()
        with client2.session_transaction() as session:
            session["account"] = staff_account
            session["permissions"] = ["staff_view"]

        with patch("app.utils.auth.get_current_session_account", return_value=staff_account), \
             patch("app.utils.auth.get_user_permissions", return_value={"staff_view"}):
            response = client2.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 403
            assert response.get_json()["error"] == "Permission denied"

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM Customer WHERE customer_id = %s", (customer_id,))


def test_sse_missing_token_closes_stream_no_fabricated_status(app):
    """Missing token closes stream without emitting a fabricated status.
    Token is deleted after stream is established."""
    tag = uuid.uuid4().hex[:10]
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
                    (f"SSE Missing {tag}", f"SMM{tag}", f"{tag}@example.invalid"),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO customer_account
                        (customer_id, username, password_hash, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (customer_id, f"sse_missing_{tag}", hash_password("SSE-test-Password9")),
                )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                cursor.execute(
                    """
                    INSERT INTO Token (queue_id, customer_id, token_number, status)
                    VALUES (1, %s, %s, 'waiting')
                    """,
                    (customer_id, next_number),
                )
                token_id = cursor.lastrowid

        with app.test_client() as client:
            login = client.post(
                "/api/auth/customer/login",
                json={"username": f"sse_missing_{tag}", "password": "SSE-test-Password9"},
            )
            assert login.status_code == 200

            # Establish the stream first
            response = client.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 200
            assert response.mimetype == "text/event-stream"

            # Now delete the token while stream is active
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))

            # Stream should close without emitting a fabricated "deleted" status
            events = _collect_sse_events(response, max_events=3)
            data_events = [e for e in events if e.startswith("data: ")]
            # Should receive 0 data events after token deletion (stream closes cleanly)
            for e in data_events:
                data = json.loads(e[6:])
                assert "deleted" not in data.get("status", ""), f"Fabricated status leaked: {data}"

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM customer_account WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM Customer WHERE customer_id = %s", (customer_id,))


def test_sse_terminal_token_emits_final_state_then_closes(app):
    """Terminal token (completed/no-show) emits its final state before stream closes."""
    tag = uuid.uuid4().hex[:10]
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
                    (f"SSE Terminal {tag}", f"STM{tag}", f"{tag}@example.invalid"),
                )
                customer_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO customer_account
                        (customer_id, username, password_hash, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (customer_id, f"sse_term_{tag}", hash_password("SSE-test-Password9")),
                )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(token_number), 0) AS max_number
                    FROM Token WHERE queue_id = 1
                    """
                )
                next_number = cursor.fetchone()["max_number"] + 1

                cursor.execute(
                    """
                    INSERT INTO Token (queue_id, customer_id, token_number, status)
                    VALUES (1, %s, %s, 'completed')
                    """,
                    (customer_id, next_number),
                )
                token_id = cursor.lastrowid

        with app.test_client() as client:
            login = client.post(
                "/api/auth/customer/login",
                json={"username": f"sse_term_{tag}", "password": "SSE-test-Password9"},
            )
            assert login.status_code == 200

            response = client.get(f"/api/tokens/{token_id}/stream")
            assert response.status_code == 200

            events = _collect_sse_events(response, max_events=3)
            data_events = [e for e in events if e.startswith("data: ")]
            assert len(data_events) >= 1, "Expected at least one data event for terminal token"

            # First event should be the terminal state
            final_data = json.loads(data_events[0][6:])
            assert final_data["status"] == "completed"
            assert "waiting_ahead" in final_data
            assert "estimated_wait_minutes" in final_data

            # Stream should close after terminal state (no more data events)
            assert len(data_events) == 1, "Stream should close after terminal status"

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Queue_History WHERE token_id = %s", (token_id,))
                    cursor.execute("DELETE FROM Token WHERE token_id = %s", (token_id,))
        if customer_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute("DELETE FROM Notification WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM customer_account WHERE customer_id = %s", (customer_id,))
                    cursor.execute("DELETE FROM Customer WHERE customer_id = %s", (customer_id,))