"""QueueX Stored Procedure Wrappers
Python interface for calling MySQL stored procedures."""

from typing import Optional, Dict, Any

from app.utils.db import get_cursor, get_db


def _read_token_output(cursor) -> Dict[str, Any]:
    """Read token OUT parameters after a stored procedure call."""
    cursor.fetchall()

    # Drain any remaining result sets before executing another query.
    while cursor.nextset():
        cursor.fetchall()

    cursor.execute("""
        SELECT
            @p_token_id AS token_id,
            @p_token_number AS token_number
    """)

    row = cursor.fetchone()

    if not row or row["token_id"] is None:
        raise RuntimeError("Token creation failed")

    return {
        "token_id": row["token_id"],
        "token_number": row["token_number"],
    }


def _create_token_internal(
    cursor,
    queue_id: int,
    customer_id: int,
) -> Dict[str, Any]:
    """
    Create a token without starting or committing a transaction.

    The caller must provide a cursor belonging to the transaction
    that should include token creation.
    """
    cursor.execute(
        """
        CALL create_token_internal(
            %s, %s, @p_token_id, @p_token_number
        )
        """,
        (queue_id, customer_id),
    )

    return _read_token_output(cursor)


def issue_token(
    queue_id: int,
    customer_id: int,
) -> Dict[str, Any]:
    """
    Issue a token using the transaction-owning issue_token procedure.

    Use this wrapper for standalone token creation.
    """
    with get_cursor() as cursor:
        cursor.execute(
            """
            CALL issue_token(
                %s, %s, @p_token_id, @p_token_number
            )
            """,
            (queue_id, customer_id),
        )

        token = _read_token_output(cursor)

        return {
            "token_id": token["token_id"],
            "token_number": token["token_number"],
            "status": "waiting",
        }


def create_customer_and_token(
    full_name: str,
    phone: str,
    email: Optional[str],
    queue_id: int,
) -> Dict[str, Any]:
    """
    Atomically create a customer and issue a token.

    Customer creation, token creation, queue-counter updates, and
    queue-history insertion use the same database transaction.
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                INSERT INTO Customer (full_name, phone, email)
                VALUES (%s, %s, %s)
                """,
                (full_name, phone, email),
            )

            customer_id = cursor.lastrowid

            # Do not call issue_token here: it owns and commits
            # its own transaction. Use the transaction-neutral helper.
            token = _create_token_internal(
                cursor,
                queue_id,
                customer_id,
            )

            return {
                "customer_id": customer_id,
                "token_id": token["token_id"],
                "token_number": token["token_number"],
                "status": "waiting",
            }

        finally:
            cursor.close()


def call_next_token(
    counter_id: int,
    staff_id: int,
) -> Optional[Dict[str, Any]]:
    """
    Call the next waiting token for a counter.

    Returns token and customer details, or None if no token is waiting.
    """
    with get_cursor() as cursor:
        try:
            cursor.callproc(
                "call_next_token",
                (counter_id, staff_id),
            )

            for result in cursor.stored_results():
                row = result.fetchone()

                if not row:
                    continue

                token_id = (
                    row.get("token_id")
                    or row.get("v_token_id")
                )

                if token_id is None:
                    continue

                cursor.execute(
                    """
                    SELECT
                        t.token_id,
                        t.token_number,
                        t.queue_id,
                        t.customer_id,
                        c.full_name AS customer_name,
                        c.phone AS customer_phone
                    FROM Token t
                    JOIN Customer c
                        ON t.customer_id = c.customer_id
                    WHERE t.token_id = %s
                    """,
                    (token_id,),
                )

                return cursor.fetchone()

            return None

        except Exception as exc:
            if "No waiting tokens" in str(exc):
                return None
            raise


def complete_token(token_id: int) -> bool:
    """Mark a token as completed."""
    with get_cursor() as cursor:
        cursor.callproc("complete_token", (token_id,))

    return True


def mark_no_show(
    token_id: int,
    window_minutes: int = 5,
) -> bool:
    """
    Mark a called token as no-show if the allowed window has elapsed.

    Returns False if the no-show window has not elapsed.
    """
    with get_cursor() as cursor:
        try:
            cursor.callproc(
                "mark_no_show",
                (token_id, window_minutes),
            )
            return True

        except Exception as exc:
            if "No-show window has not elapsed" in str(exc):
                return False
            raise


def serve_token(token_id: int) -> bool:
    """Mark a called token as serving."""
    with get_cursor() as cursor:
        cursor.callproc("serve_token", (token_id,))

    return True


def get_token_status(
    token_id: int,
) -> Optional[Dict[str, Any]]:
    """Get current token status and related details."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                t.*,
                c.full_name AS customer_name,
                c.phone AS customer_phone,
                q.name AS queue_name,
                b.name AS branch_name,
                ct.name AS counter_name,
                s.full_name AS staff_name
            FROM Token t
            JOIN Customer c
                ON t.customer_id = c.customer_id
            JOIN Queue q
                ON t.queue_id = q.queue_id
            JOIN Branch b
                ON q.branch_id = b.branch_id
            LEFT JOIN Counter ct
                ON t.counter_id = ct.counter_id
            LEFT JOIN Staff s
                ON t.staff_id = s.staff_id
            WHERE t.token_id = %s
            """,
            (token_id,),
        )

        return cursor.fetchone()


def get_queue_waiting_count(queue_id: int) -> int:
    """Get the number of waiting tokens in a queue."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM Token
            WHERE queue_id = %s
              AND status = 'waiting'
            """,
            (queue_id,),
        )

        row = cursor.fetchone()
        return row["cnt"] if row else 0


def get_estimated_wait_time(queue_id: int) -> int:
    """
    Estimate wait time in minutes using service duration multiplied
    by the number of waiting tokens.
    """
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                s.estimated_duration_minutes,
                (
                    SELECT COUNT(*)
                    FROM Token
                    WHERE queue_id = q.queue_id
                      AND status = 'waiting'
                ) AS waiting_count
            FROM Queue q
            JOIN Service s
                ON q.service_id = s.service_id
            WHERE q.queue_id = %s
            """,
            (queue_id,),
        )

        row = cursor.fetchone()

        if row:
            return (
                row["estimated_duration_minutes"]
                * row["waiting_count"]
            )

        return 0


def create_customer_request(
    customer_id: int,
    queue_id: int,
) -> int:
    """
    Create a draft request and add the active documents configured
    for the service associated with the selected queue.
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT q.queue_id, q.service_id
                FROM Queue q
                WHERE q.queue_id = %s
                  AND q.is_active = 1
                """,
                (queue_id,),
            )

            queue = cursor.fetchone()

            if not queue:
                raise ValueError("Invalid or inactive queue")

            cursor.execute(
                """
                SELECT customer_id
                FROM Customer
                WHERE customer_id = %s
                """,
                (customer_id,),
            )

            if not cursor.fetchone():
                raise ValueError("Customer not found")

            cursor.execute(
                """
                INSERT INTO customer_request (
                    customer_id,
                    queue_id,
                    request_status
                )
                VALUES (%s, %s, 'draft')
                """,
                (customer_id, queue_id),
            )

            request_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO request_document (
                    request_id,
                    document_id,
                    readiness_status
                )
                SELECT %s, d.document_id, 'pending'
                FROM service_document sd
                JOIN document d
                    ON d.document_id = sd.document_id
                WHERE sd.service_id = %s
                  AND d.is_active = 1
                """,
                (request_id, queue["service_id"]),
            )

            return request_id

        finally:
            cursor.close()


def update_request_document_status(
    request_id: int,
    document_id: int,
    readiness_status: str,
) -> None:
    """
    Update document readiness.

    Allowed statuses: available, missing.
    """
    if readiness_status not in ("available", "missing"):
        raise ValueError(
            "Status must be 'available' or 'missing'"
        )

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT rd.request_id
                FROM request_document rd
                JOIN customer_request cr
                    ON cr.request_id = rd.request_id
                JOIN Queue q
                    ON q.queue_id = cr.queue_id
                JOIN service_document sd
                    ON sd.service_id = q.service_id
                   AND sd.document_id = rd.document_id
                JOIN document d
                    ON d.document_id = rd.document_id
                WHERE rd.request_id = %s
                  AND rd.document_id = %s
                  AND d.is_active = 1
                """,
                (request_id, document_id),
            )

            if not cursor.fetchone():
                raise ValueError(
                    "Document is not valid for this request"
                )

            cursor.execute(
                """
                UPDATE request_document
                SET readiness_status = %s,
                    confirmed_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                  AND document_id = %s
                """,
                (readiness_status, request_id, document_id),
            )

        finally:
            cursor.close()


def confirm_customer_request_readiness(
    request_id: int,
) -> None:
    """Confirm readiness only when all required documents are available."""
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT request_status, readiness_confirmed
                FROM customer_request
                WHERE request_id = %s
                FOR UPDATE
                """,
                (request_id,),
            )

            customer_request = cursor.fetchone()

            if not customer_request:
                raise ValueError("Customer request not found")

            if customer_request["request_status"] != "draft":
                raise ValueError(
                    "Only draft requests can be confirmed"
                )

            cursor.execute(
                """
                SELECT COUNT(*) AS incomplete_count
                FROM service_document sd
                JOIN Queue q
                    ON q.service_id = sd.service_id
                JOIN customer_request cr
                    ON cr.queue_id = q.queue_id
                LEFT JOIN request_document rd
                    ON rd.request_id = cr.request_id
                   AND rd.document_id = sd.document_id
                WHERE cr.request_id = %s
                  AND sd.is_required = 1
                  AND (
                      rd.document_id IS NULL
                      OR rd.readiness_status <> 'available'
                  )
                """,
                (request_id,),
            )

            result = cursor.fetchone()

            if result["incomplete_count"] > 0:
                raise ValueError(
                    "All required documents must be available"
                )

            cursor.execute(
                """
                UPDATE customer_request
                SET readiness_confirmed = 1,
                    request_status = 'ready',
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                """,
                (request_id,),
            )

        finally:
            cursor.close()


def _link_token_to_request(
    cursor,
    request_id: int,
    token_id: int,
) -> None:
    """Link a newly issued token to a ready customer request."""
    cursor.execute(
        """
        UPDATE customer_request
        SET token_id = %s,
            request_status = 'queued',
            updated_at = CURRENT_TIMESTAMP
        WHERE request_id = %s
          AND token_id IS NULL
          AND request_status = 'ready'
        """,
        (token_id, request_id),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Could not link token to customer request"
        )


def queue_customer_request(
    request_id: int,
) -> Dict[str, Any]:
    """
    Issue a token for a ready request and link it in the same
    database transaction.

    If token creation or linking fails, get_db() must roll back
    the token, queue-counter update, history entry, and request changes.
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Lock the request so simultaneous calls cannot queue it twice.
            cursor.execute(
                """
                SELECT
                    cr.request_id,
                    cr.customer_id,
                    cr.queue_id,
                    cr.token_id,
                    cr.request_status,
                    cr.readiness_confirmed
                FROM customer_request cr
                WHERE cr.request_id = %s
                FOR UPDATE
                """,
                (request_id,),
            )

            customer_request = cursor.fetchone()

            if not customer_request:
                raise ValueError("Customer request not found")

            # Idempotency: return the existing token if already queued.
            if customer_request["token_id"] is not None:
                cursor.execute(
                    """
                    SELECT token_id, token_number, queue_id, customer_id
                    FROM Token
                    WHERE token_id = %s
                    """,
                    (customer_request["token_id"],),
                )

                existing_token = cursor.fetchone()

                if existing_token:
                    return {
                        "request_id": request_id,
                        "token_id": existing_token["token_id"],
                        "token_number": existing_token["token_number"],
                        "queue_id": existing_token["queue_id"],
                        "request_status": customer_request["request_status"],
                        "message": "Request already has a token",
                    }

                raise RuntimeError(
                    "Request references a token that does not exist"
                )

            if customer_request["request_status"] != "ready":
                raise ValueError(
                    "Only ready requests can be queued"
                )

            if not customer_request["readiness_confirmed"]:
                raise ValueError(
                    "Request readiness has not been confirmed"
                )

            # Revalidate every required document, including missing rows.
            cursor.execute(
                """
                SELECT COUNT(*) AS incomplete_count
                FROM service_document sd
                JOIN Queue q
                    ON q.service_id = sd.service_id
                LEFT JOIN request_document rd
                    ON rd.request_id = %s
                   AND rd.document_id = sd.document_id
                WHERE q.queue_id = %s
                  AND sd.is_required = 1
                  AND (
                      rd.document_id IS NULL
                      OR rd.readiness_status <> 'available'
                  )
                """,
                (request_id, customer_request["queue_id"]),
            )

            result = cursor.fetchone()

            if result["incomplete_count"] > 0:
                raise ValueError(
                    "All required documents must be available"
                )

            # IMPORTANT: this procedure does not commit independently.
            # Token creation remains inside the current get_db() transaction.
            token = _create_token_internal(
                cursor,
                customer_request["queue_id"],
                customer_request["customer_id"],
            )

            # Any failure here raises an exception, allowing get_db()
            # to roll back token creation and the queue counter together.
            _link_token_to_request(
                cursor,
                request_id,
                token["token_id"],
            )

            return {
                "request_id": request_id,
                "token_id": token["token_id"],
                "token_number": token["token_number"],
                "queue_id": customer_request["queue_id"],
                "request_status": "queued",
                "message": "Customer request queued successfully",
            }

        finally:
            cursor.close()


def call_specific_token(counter_id: int, staff_id: int, token_number: int) -> Optional[Dict[str, Any]]:
    """Call a specific token by number for a counter."""
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Validate counter
            cursor.execute(
                """SELECT c.counter_id, c.queue_id, c.is_active
                   FROM Counter c
                   WHERE c.counter_id = %s""",
                (counter_id,)
            )

            counter = cursor.fetchone()

            if not counter or not counter['is_active']:
                return None

            # Find the token
            cursor.execute(
                """SELECT t.token_id, t.token_number, t.queue_id, t.status, t.customer_id, t.counter_id
                   FROM Token t
                   WHERE t.queue_id = %s
                     AND t.token_number = %s""",
                (counter['queue_id'], token_number)
            )

            token = cursor.fetchone()

            if not token:
                return None

            if token['status'] != 'waiting':
                return None

            # Call the token
            cursor.execute(
                """UPDATE Token
                   SET status = 'called',
                       counter_id = %s,
                       staff_id = %s,
                       called_at = CURRENT_TIMESTAMP
                   WHERE token_id = %s
                     AND status = 'waiting'""",
                (counter_id, staff_id, token['token_id'])
            )

            if cursor.rowcount != 1:
                return None

            # Log history
            cursor.execute(
                """INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
                   VALUES (%s, 'waiting', 'called', %s)""",
                (token['token_id'], staff_id)
            )

            # Get updated token
            cursor.execute(
                """SELECT t.*, c.full_name AS customer_name, q.name AS queue_name, s.name AS service_name
                   FROM Token t
                   JOIN Customer c ON t.customer_id = c.customer_id
                   JOIN Queue q ON t.queue_id = q.queue_id
                   JOIN Service s ON q.service_id = s.service_id
                   WHERE t.token_id = %s""",
                (token['token_id'],)
            )

            return cursor.fetchone()

        finally:
            cursor.close()


def reissue_token(token_id: int) -> bool:
    """Reissue a token - put it back in waiting queue."""
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Get current token
            cursor.execute(
                """SELECT token_id, status, counter_id, queue_id, staff_id
                   FROM Token
                   WHERE token_id = %s""",
                (token_id,)
            )

            token = cursor.fetchone()

            if not token:
                return False

            if token['status'] not in ('called', 'serving'):
                return False

            # Reissue the token
            cursor.execute(
                """UPDATE Token
                   SET status = 'waiting',
                       counter_id = NULL,
                       staff_id = NULL,
                       called_at = NULL
                   WHERE token_id = %s""",
                (token_id,)
            )

            if cursor.rowcount != 1:
                return False

            # Log history
            cursor.execute(
                """INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
                   VALUES (%s, %s, 'waiting', %s)""",
                (token_id, token['status'], token.get('staff_id'))
            )

            return True

        finally:
            cursor.close()