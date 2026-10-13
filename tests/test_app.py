
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

    with db.get_cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        result = cursor.fetchone()

    assert result["database_name"] == "queuex_test"


def test_normal_token_endpoint_issues_token(client, app):
    from app.utils.db import get_cursor

    token_id = None

    try:
        response = client.post(
            "/api/tokens",
            json={"queue_id": 1, "customer_id": 1},
        )

        assert response.status_code == 201, response.get_json()

        result = response.get_json()
        assert result["token_id"] is not None
        assert result["token_number"] is not None
        assert result["status"] == "waiting"

        token_id = result["token_id"]

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT queue_id, customer_id, token_number, status
                    FROM token
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                token = cursor.fetchone()

                assert token is not None
                assert token["queue_id"] == 1
                assert token["customer_id"] == 1
                assert token["token_number"] == result["token_number"]
                assert token["status"] == "waiting"

                cursor.execute(
                    """
                    SELECT new_status
                    FROM queue_history
                    WHERE token_id = %s
                    ORDER BY history_id
                    """,
                    (token_id,),
                )
                history = cursor.fetchall()
                assert any(
                    row["new_status"] == "waiting"
                    for row in history
                )

    finally:
        if token_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM notification WHERE token_id = %s",
                        (token_id,),
                    )
                    cursor.execute(
                        "DELETE FROM queue_history WHERE token_id = %s",
                        (token_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE token_id = %s",
                        (token_id,),
                    )
                    cursor.execute(
                        "DELETE FROM token WHERE token_id = %s",
                        (token_id,),
                    )


def test_create_customer_request_rejects_missing_ids(client):
    response = client.post("/api/customer-requests", json={})
    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Valid customer_id and queue_id are required"
    )


def test_queue_nonexistent_request_returns_bad_request(client):
    response = client.post("/api/customer-requests/99999/queue")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Customer request not found"


def test_create_and_retrieve_customer_request(client, app):
    from app.utils.db import get_cursor

    request_id = None

    try:
        response = client.post(
            "/api/customer-requests",
            json={"customer_id": 1, "queue_id": 1},
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
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM request_document WHERE request_id = %s",
                        (request_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE request_id = %s",
                        (request_id,),
                    )


def test_readiness_rejected_when_required_documents_are_pending(client, app):
    from app.utils.db import get_cursor

    request_id = None

    try:
        created = client.post(
            "/api/customer-requests",
            json={"customer_id": 1, "queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()

        request_id = created.get_json()["request_id"]

        # Readiness must be rejected while required documents are pending.
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
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM request_document WHERE request_id = %s",
                        (request_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE request_id = %s",
                        (request_id,),
                    )


def test_readiness_confirmed_when_all_required_documents_are_available(
    client, app
):
    from app.utils.db import get_cursor

    request_id = None

    try:
        created = client.post(
            "/api/customer-requests",
            json={"customer_id": 1, "queue_id": 1},
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
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM request_document WHERE request_id = %s",
                        (request_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE request_id = %s",
                        (request_id,),
                    )
                
def test_queueing_request_twice_reuses_existing_token(client, app):
    from app.utils.db import get_cursor

    request_id = None
    token_id = None

    try:
        # Create a fresh draft request.
        created = client.post(
            "/api/customer-requests",
            json={"customer_id": 1, "queue_id": 1},
        )
        assert created.status_code == 201, created.get_json()
        request_id = created.get_json()["request_id"]

        # Mark all required documents as available.
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

        # Confirm readiness.
        confirmed = client.post(
            f"/api/customer-requests/{request_id}/confirm-readiness"
        )
        assert confirmed.status_code == 200, confirmed.get_json()

        # Queue the same request twice.
        first = client.post(f"/api/customer-requests/{request_id}/queue")
        assert first.status_code == 200, first.get_json()
        first_result = first.get_json()
        token_id = first_result["token_id"]

        second = client.post(f"/api/customer-requests/{request_id}/queue")
        assert second.status_code == 200, second.get_json()
        second_result = second.get_json()

        # Both calls must return the same token.
        assert second_result["token_id"] == token_id
        assert second_result["token_number"] == first_result["token_number"]

        with app.app_context():
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS token_count FROM customer_request "
                    "WHERE request_id = %s AND token_id = %s",
                    (request_id, token_id),
                )
                assert cursor.fetchone()["token_count"] == 1

                cursor.execute(
                    "SELECT COUNT(*) AS token_count FROM token "
                    "WHERE token_id = %s",
                    (token_id,),
                )
                assert cursor.fetchone()["token_count"] == 1

    
    
    finally:
        if request_id is not None:
            with app.app_context():
                with get_cursor() as cursor:
                    cursor.execute(
                        "SELECT token_id FROM customer_request "
                        "WHERE request_id = %s",
                        (request_id,),
                    )
                    row = cursor.fetchone()
                    test_token_id = (
                        row["token_id"] if row else None
                    )

                    # Delete dependent rows before their parent records.
                    if test_token_id is not None:
                        cursor.execute(
                            "DELETE FROM notification WHERE token_id = %s",
                            (test_token_id,),
                        )
                        cursor.execute(
                            "DELETE FROM queue_history WHERE token_id = %s",
                            (test_token_id,),
                        )

                    cursor.execute(
                        "DELETE FROM request_document WHERE request_id = %s",
                        (request_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_request WHERE request_id = %s",
                        (request_id,),
                    )

                    if test_token_id is not None:
                        cursor.execute(
                            "DELETE FROM token WHERE token_id = %s",
                            (test_token_id,),
                        )
