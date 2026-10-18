def test_queue_request_rolls_back_token_if_linking_fails(
    client, app, monkeypatch
):
    """A failed request-to-token link must roll back token creation."""
    from app.utils import procedures
    from app.utils.db import get_cursor

    def _login_customer(client, username="aarav", password="password123"):
        """Helper to log in a customer and return the session."""
        response = client.post(
            "/api/auth/customer/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200
        return response.get_json()

    request_id = None
    token_ids_before = set()

    try:
        # 1. Create a customer request.
        _login_customer(client)
        created = client.post(
            "/api/customer-requests",
            json={"customer_id": 1, "queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        # 2. Retrieve the required documents.
        details = client.get(
            f"/api/customer-requests/{request_id}"
        )
        assert details.status_code == 200, details.get_json()

        # 3. Mark all listed documents as available.
        for document in details.get_json()["documents"]:
            response = client.patch(
                f"/api/customer-requests/{request_id}/documents",
                json={
                    "document_id": document["document_id"],
                    "readiness_status": "available",
                },
            )
            assert response.status_code == 200, response.get_json()

        # 4. Confirm readiness.
        confirmed = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )
        assert confirmed.status_code == 200, confirmed.get_json()

        # 5. Record the existing tokens and queue counter.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute("SELECT token_id FROM `Token`")
                token_ids_before = {
                    row["token_id"] for row in cursor.fetchall()
                }

                cursor.execute(
                    "SELECT last_token_number FROM Queue WHERE queue_id = %s",
                    (1,),
                )
                queue_before = cursor.fetchone()
                assert queue_before is not None
                last_token_before = queue_before["last_token_number"]

        # 6. Simulate a failure after token creation, during linking.
        def fail_link(*args, **kwargs):
            raise RuntimeError("simulated token-link failure")

        monkeypatch.setattr(
            procedures,
            "_link_token_to_request",
            fail_link,
        )

        response = client.post(
            f"/api/customer-requests/{request_id}/queue"
        )

        # The simulated failure must be reported as an error.
        assert response.status_code >= 400, response.get_json()

        # Restore the real helper before inspecting the database.
        monkeypatch.undo()

        # 7. Verify the request and token creation were rolled back.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT token_id, request_status
                    FROM customer_request
                    WHERE request_id = %s
                    """,
                    (request_id,),
                )
                request = cursor.fetchone()

                assert request is not None
                assert request["token_id"] is None
                assert request["request_status"] == "ready"

                cursor.execute("SELECT token_id FROM `Token`")
                token_ids_after = {
                    row["token_id"] for row in cursor.fetchall()
                }
                assert token_ids_after == token_ids_before

                cursor.execute(
                    """
                    SELECT last_token_number
                    FROM Queue
                    WHERE queue_id = %s
                    """,
                    (1,),
                )
                queue_after = cursor.fetchone()

                assert queue_after is not None
                assert (
                    queue_after["last_token_number"]
                    == last_token_before
                )

    finally:
        # Restore patches even if an assertion fails.
        monkeypatch.undo()

        # Remove only the test request; do not reset queue counters
        # or delete pre-existing tokens.
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM request_document
                        WHERE request_id = %s
                        """,
                        (request_id,),
                    )
                    cursor.execute(
                        """
                        DELETE FROM customer_request
                        WHERE request_id = %s
                        """,
                        (request_id,),
                    )