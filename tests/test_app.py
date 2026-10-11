
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
