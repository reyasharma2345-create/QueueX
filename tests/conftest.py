
import os

# Force the isolated test database before importing the application.
os.environ["DB_NAME"] = "queuex_test"
os.environ["TEST_DB_NAME"] = "queuex_test"

import pytest


@pytest.fixture(scope="session")
def app():
    from app import create_app
    from app.utils import db

    application = create_app("testing")

    # Safety checks: never run these tests against production.
    assert application.config["DB_NAME"] == "queuex_test"
    assert db._config.database == "queuex_test"

    with db.get_cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        result = cursor.fetchone()

    assert result["database_name"] == "queuex_test", (
        "Tests must never run against the production database."
    )

    application.config.update(TESTING=True)
    yield application


@pytest.fixture(scope="session")
def required_document_seed(app):
    from app.utils.db import get_cursor

    with app.app_context():
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT d.document_id, d.name, d.is_active,
                       sd.is_required
                FROM document d
                LEFT JOIN service_document sd
                    ON sd.document_id = d.document_id
                   AND sd.service_id = 1
                WHERE d.document_id IN (1, 2, 3)
                ORDER BY d.document_id
            """)
            rows = cursor.fetchall()

    expected = [
        (1, "Identity Proof"),
        (2, "Address Proof"),
        (3, "PAN Card"),
    ]

    actual = [
        (row["document_id"], row["name"])
        for row in rows
        if row["is_active"] and row["is_required"]
    ]

    assert actual == expected, (
        "queuex_test is missing required document seed data. "
        "Apply the test database schema and seed data before running tests."
    )


@pytest.fixture
def client(app, required_document_seed):
    return app.test_client()
