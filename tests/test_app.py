def _login_customer(client, username="aarav", password="password123"):
    """Log in as the seeded test customer."""
    response = client.post(
        "/api/auth/customer/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _delete_customer_request(app, request_id):
    """Clean up a request and any token created for it."""
    if request_id is None:
        return

    from app.utils.db import get_cursor

    with app.app_context():
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT token_id
                FROM customer_request
                WHERE request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            token_id = row["token_id"] if row else None

            if token_id is not None:
                cursor.execute(
                    "DELETE FROM Notification WHERE token_id = %s",
                    (token_id,),
                )
                cursor.execute(
                    "DELETE FROM Queue_History WHERE token_id = %s",
                    (token_id,),
                )

            cursor.execute(
                "DELETE FROM request_document WHERE request_id = %s",
                (request_id,),
            )
            cursor.execute(
                "DELETE FROM customer_request WHERE request_id = %s",
                (request_id,),
            )

            if token_id is not None:
                cursor.execute(
                    "DELETE FROM `Token` WHERE token_id = %s",
                    (token_id,),
                )


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "queuex",
    }


def test_database_is_queuex_test(app):
    from app.utils import db

    assert app.config["DB_NAME"] == "queuex_test"
    assert db._config.database == "queuex_test"

    with app.app_context():
        with db.get_cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS database_name")
            result = cursor.fetchone()

    assert result["database_name"] == "queuex_test"


def test_create_customer_request_rejects_missing_ids(client):
    _login_customer(client)

    response = client.post("/api/customer-requests", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "A valid queue_id is required"


def test_queue_nonexistent_request_returns_bad_request(client):
    _login_customer(client)

    response = client.post("/api/customer-requests/99999/queue")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Request not found"


def test_create_and_retrieve_customer_request(client, app):
    request_id = None

    try:
        _login_customer(client)

        response = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert response.status_code == 201, response.get_json()

        request_id = response.get_json()["request_id"]
        assert response.get_json()["request_status"] == "draft"

        response = client.get(f"/api/customer-requests/{request_id}")
        assert response.status_code == 200, response.get_json()

        result = response.get_json()
        assert result["request"]["request_id"] == request_id
        assert result["request"]["request_status"] == "draft"
        assert isinstance(result["documents"], list)
        assert len(result["documents"]) == 3
        assert all(
            document["readiness_status"] == "pending"
            for document in result["documents"]
        )
    finally:
        _delete_customer_request(app, request_id)


def test_readiness_rejected_when_required_documents_are_pending(client, app):
    request_id = None

    try:
        _login_customer(client)

        created = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        details = client.get(f"/api/customer-requests/{request_id}")
        assert details.status_code == 200, details.get_json()

        documents = details.get_json()["documents"]
        assert len(documents) == 3
        assert all(
            document["readiness_status"] == "pending"
            for document in documents
        )

        response = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )

        assert response.status_code == 400, response.get_json()
        assert response.get_json()["error"] == (
            "All required documents must be available"
        )
    finally:
        _delete_customer_request(app, request_id)


def test_readiness_confirmed_when_all_required_documents_are_available(
    client, app
):
    request_id = None

    try:
        _login_customer(client)

        created = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        details = client.get(f"/api/customer-requests/{request_id}")
        assert details.status_code == 200, details.get_json()

        documents = details.get_json()["documents"]
        assert len(documents) == 3

        for document in documents:
            updated = client.patch(
                f"/api/customer-requests/{request_id}/documents",
                json={
                    "document_id": document["document_id"],
                    "readiness_status": "available",
                },
            )
            assert updated.status_code == 200, updated.get_json()

        response = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )

        assert response.status_code == 200, response.get_json()
        assert response.get_json()["request_status"] == "ready"
        assert response.get_json()["readiness_confirmed"] is True

        details = client.get(f"/api/customer-requests/{request_id}")
        assert details.status_code == 200, details.get_json()

        request = details.get_json()["request"]
        assert request["request_status"] == "ready"
        assert request["readiness_confirmed"] in (1, True)
    finally:
        _delete_customer_request(app, request_id)


def test_queueing_request_twice_reuses_existing_token(client, app):
    request_id = None

    try:
        _login_customer(client)

        created = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        details = client.get(f"/api/customer-requests/{request_id}")
        assert details.status_code == 200, details.get_json()

        for document in details.get_json()["documents"]:
            updated = client.patch(
                f"/api/customer-requests/{request_id}/documents",
                json={
                    "document_id": document["document_id"],
                    "readiness_status": "available",
                },
            )
            assert updated.status_code == 200, updated.get_json()

        confirmed = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )
        assert confirmed.status_code == 200, confirmed.get_json()

        first = client.post(
            f"/api/customer-requests/{request_id}/queue"
        )
        assert first.status_code == 200, first.get_json()
        first_result = first.get_json()

        second = client.post(
            f"/api/customer-requests/{request_id}/queue"
        )
        assert second.status_code == 200, second.get_json()
        second_result = second.get_json()

        assert second_result["token_id"] == first_result["token_id"]
        assert second_result["token_number"] == first_result["token_number"]

        from app.utils.db import get_cursor

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS token_count
                    FROM customer_request
                    WHERE request_id = %s AND token_id = %s
                    """,
                    (request_id, first_result["token_id"]),
                )
                assert cursor.fetchone()["token_count"] == 1

                cursor.execute(
                    """
                    SELECT COUNT(*) AS token_count
                    FROM `Token`
                    WHERE token_id = %s
                    """,
                    (first_result["token_id"],),
                )
                assert cursor.fetchone()["token_count"] == 1
    finally:
        _delete_customer_request(app, request_id)


def test_document_update_rejects_invalid_service_document(
    client, app, monkeypatch
):
    from app import routes

    def reject_invalid_document(*args, **kwargs):
        raise ValueError("Document is not valid for this request")

    monkeypatch.setattr(
        routes,
        "update_request_document_status",
        reject_invalid_document,
    )

    request_id = None

    try:
        _login_customer(client)

        created = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        response = client.patch(
            f"/api/customer-requests/{request_id}/documents",
            json={
                "document_id": 999,
                "readiness_status": "available",
            },
        )

        assert response.status_code == 400, response.get_json()
        assert response.get_json()["error"] == (
            "Document is not valid for this request"
        )
    finally:
        _delete_customer_request(app, request_id)

def test_customer_cannot_access_another_customers_request(client, app):
    """A customer cannot read or modify another customer's request."""
    from app.utils.auth import hash_password
    from app.utils.db import get_cursor

    request_id = None
    second_username = "ownership_test_customer_b"
    second_password = "ownership_test_password_123"

    try:
        # Create a request as customer A.
        _login_customer(client, username="aarav")

        created = client.post(
            "/api/customer-requests",
            json={"queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        # Create a separate customer B in the isolated test database.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account_id
                    FROM customer_account
                    WHERE username = %s
                    """,
                    (second_username,),
                )
                existing = cursor.fetchone()

                if existing is None:
                    cursor.execute(
                        """
                        INSERT INTO Customer (full_name)
                        VALUES (%s)
                        """,
                        ("Ownership Test Customer B",),
                    )
                    customer_id = cursor.lastrowid

                    cursor.execute(
                        """
                        INSERT INTO customer_account
                            (customer_id, username, password_hash, is_active)
                        VALUES (%s, %s, %s, 1)
                        """,
                        (
                            customer_id,
                            second_username,
                            hash_password(second_password),
                        ),
                    )

        # Authenticate as customer B using the real login endpoint.
        login_b = client.post(
            "/api/auth/customer/login",
            json={
                "username": second_username,
                "password": second_password,
            },
        )
        assert login_b.status_code == 200, login_b.get_json()

        # B must not be able to read A's request.
        response = client.get(
            f"/api/customer-requests/{request_id}"
        )
        assert response.status_code == 404, response.get_json()

        # B must not be able to update A's document status.
        response = client.patch(
            f"/api/customer-requests/{request_id}/documents",
            json={
                "document_id": 1,
                "readiness_status": "available",
            },
        )
        assert response.status_code == 404, response.get_json()

        # B must not be able to confirm A's readiness.
        response = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )
        assert response.status_code == 404, response.get_json()

        # B must not be able to queue A's request.
        response = client.post(
            f"/api/customer-requests/{request_id}/queue"
        )
        assert response.status_code == 404, response.get_json()

        # Verify A's request still exists and remains unchanged.
        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT customer_id, request_status, token_id,
                           readiness_confirmed
                    FROM customer_request
                    WHERE request_id = %s
                    """,
                    (request_id,),
                )
                request_row = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM request_document
                    WHERE request_id = %s
                      AND readiness_status <> 'pending'
                    """,
                    (request_id,),
                )
                changed_documents = cursor.fetchone()["total"]

        assert request_row is not None
        assert request_row["request_status"] == "draft"
        assert request_row["token_id"] is None
        assert not request_row["readiness_confirmed"]
        assert changed_documents == 0

    finally:
        _delete_customer_request(app, request_id)