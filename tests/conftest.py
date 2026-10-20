
import os
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers
# Force the isolated test database before importing the application.
os.environ["DB_NAME"] = "queuex_test"
os.environ["TEST_DB_NAME"] = "queuex_test"

import pytest

class CSRFTestClient(FlaskClient):
    """Test client that adds CSRF tokens to unsafe requests by default."""

    auto_csrf = True

    def open(self, *args, **kwargs):
        method = (kwargs.get("method") or "GET").upper()

        if self.auto_csrf and method not in (
            "GET", "HEAD", "OPTIONS", "TRACE"
        ):
            headers = Headers(kwargs.get("headers"))

            if not headers.get("X-CSRFToken"):
                token_response = super().open(
                    "/api/csrf-token",
                    method="GET",
                )

                if token_response.status_code != 200:
                    return token_response

                token_data = token_response.get_json()

                if not token_data or "csrf_token" not in token_data:
                    raise RuntimeError(
                        "CSRF token endpoint did not return a token."
                    )

                headers["X-CSRFToken"] = token_data["csrf_token"]
                kwargs["headers"] = headers

        return super().open(*args, **kwargs)


@pytest.fixture(scope="session")
def app():
    from app import create_app
    from app.utils import db

    application = create_app("testing")
    application.test_client_class = CSRFTestClient
    # Safety checks: never run these tests against production.
    assert application.config["DB_NAME"] == "queuex_test"
    assert db._config.database == "queuex_test"

    with application.app_context():
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
            cursor.execute(
                """
                SELECT d.document_id, d.name, d.is_active,
                       sd.is_required
                FROM document d
                LEFT JOIN service_document sd
                    ON sd.document_id = d.document_id
                   AND sd.service_id = 1
                WHERE d.document_id IN (1, 2, 3)
                ORDER BY d.document_id
                """
            )
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


@pytest.fixture(scope="session")
def test_customer(app, required_document_seed):
    """Ensure the expected customer account exists in queuex_test."""
    from app.utils.auth import hash_password
    from app.utils.db import get_cursor

    username = "aarav"
    password = "password123"

    with app.app_context():
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT ca.account_id
                FROM customer_account AS ca
                WHERE ca.username = %s
                """,
                (username,),
            )
            account = cursor.fetchone()

            if account is None:
                cursor.execute(
                    """
                    INSERT INTO Customer (full_name)
                    VALUES (%s)
                    """,
                    ("Aarav Test",),
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
                        username,
                        hash_password(password),
                    ),
                )

    return {"username": username, "password": password}


@pytest.fixture
def raw_client(app):
    """Create a test client without automatic CSRF tokens."""
    client = app.test_client()
    client.auto_csrf = False
    return client


@pytest.fixture
def client(app, required_document_seed, test_customer):
    """Create a CSRF-aware test client."""
    return app.test_client()
