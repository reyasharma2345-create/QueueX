"""
QueueX Stored Procedure Wrappers
Python interface for calling MySQL stored procedures.
"""

from typing import Optional, Dict, Any

from app.utils.db import get_cursor, get_db


def issue_token(queue_id: int, customer_id: int) -> Dict[str, Any]:
    """
    Issue a new token for a customer in a queue.

    Args:
        queue_id: The queue to issue token in
        customer_id: The customer receiving the token

    Returns:
        Dict with token_id, token_number, status
    """
    with get_cursor() as cursor:
        cursor.execute(
            "CALL issue_token(%s, %s, @p_token_id, @p_token_number)",
            (queue_id, customer_id)
        )

        # Consume any result sets returned by the procedure
        cursor.fetchall()

        # Get OUT parameters
        cursor.execute("""
            SELECT
                @p_token_id AS token_id,
                @p_token_number AS token_number
        """)

        row = cursor.fetchone()

        return {
            'token_id': row['token_id'],
            'token_number': row['token_number'],
            'status': 'waiting'
        }


def create_customer_and_token(
    full_name: str,
    phone: str,
    email: Optional[str],
    queue_id: int
) -> Dict[str, Any]:
    """
    Atomically create a customer and issue a token.

    Both operations use the same database transaction.

    If customer creation or token creation fails,
    the entire transaction is rolled back.
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # -------------------------------------------------
            # 1. Create customer
            # -------------------------------------------------
            cursor.execute("""
                INSERT INTO Customer (full_name, phone, email)
                VALUES (%s, %s, %s)
            """, (full_name, phone, email))

            customer_id = cursor.lastrowid

            # -------------------------------------------------
            # 2. Issue token using the SAME connection
            # -------------------------------------------------
            cursor.execute(
                "CALL issue_token(%s, %s, @p_token_id, @p_token_number)",
                (queue_id, customer_id)
            )

            # Consume procedure result set
            cursor.fetchall()

            # -------------------------------------------------
            # 3. Get token OUT parameters
            # -------------------------------------------------
            cursor.execute("""
                SELECT
                    @p_token_id AS token_id,
                    @p_token_number AS token_number
            """)

            token = cursor.fetchone()

            if not token or token['token_id'] is None:
                raise RuntimeError("Token creation failed")

            # get_db() will COMMIT here if everything succeeds
            return {
                'customer_id': customer_id,
                'token_id': token['token_id'],
                'token_number': token['token_number'],
                'status': 'waiting'
            }

        except Exception:
            # get_db() automatically performs rollback
            raise

        finally:
            cursor.close()


def call_next_token(
    counter_id: int,
    staff_id: int
) -> Optional[Dict[str, Any]]:
    """
    Call the next waiting token for a counter.

    Args:
        counter_id: The counter calling the token
        staff_id: The staff member operating the counter

    Returns:
        Dict with token_id, queue_id, token_number,
        customer info, or None if no waiting tokens
    """
    with get_cursor() as cursor:
        try:
            cursor.callproc(
                'call_next_token',
                (counter_id, staff_id)
            )

            for result in cursor.stored_results():
                row = result.fetchone()

                if row:
                    token_id = (
                        row.get('token_id')
                        or row.get('v_token_id')
                    )

                    queue_id = (
                        row.get('queue_id')
                        or row.get('v_queue_id')
                    )

                    # Get full token details
                    cursor.execute("""
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
                    """, (token_id,))

                    token = cursor.fetchone()

                    return token

            return None

        except Exception as e:
            if 'No waiting tokens' in str(e):
                return None

            raise


def complete_token(token_id: int) -> bool:
    """
    Mark a token as completed.

    Args:
        token_id: The token to complete

    Returns:
        True if successful
    """
    with get_cursor() as cursor:
        cursor.callproc(
            'complete_token',
            (token_id,)
        )

    return True


def mark_no_show(
    token_id: int,
    window_minutes: int = 5
) -> bool:
    """
    Mark a called token as no_show if the window has elapsed.

    Args:
        token_id: The token to mark as no_show
        window_minutes: No-show window in minutes

    Returns:
        True if successful, False if window not elapsed
    """
    with get_cursor() as cursor:
        try:
            cursor.callproc(
                'mark_no_show',
                (token_id, window_minutes)
            )

            return True

        except Exception as e:
            if 'No-show window has not elapsed' in str(e):
                return False

            raise


def serve_token(token_id: int) -> bool:
    """
    Mark a called token as serving.

    Args:
        token_id: The token to mark as serving

    Returns:
        True if successful
    """
    with get_cursor() as cursor:
        cursor.callproc(
            'serve_token',
            (token_id,)
        )

    return True


def get_token_status(
    token_id: int
) -> Optional[Dict[str, Any]]:
    """
    Get current token status and details.

    Args:
        token_id: The token to look up

    Returns:
        Token details dict or None if not found
    """
    with get_cursor() as cursor:
        cursor.execute("""
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
        """, (token_id,))

        return cursor.fetchone()


def get_queue_waiting_count(queue_id: int) -> int:
    """Get count of waiting tokens in a queue."""

    with get_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) AS cnt
            FROM Token
            WHERE queue_id = %s
              AND status = 'waiting'
        """, (queue_id,))

        row = cursor.fetchone()

        return row['cnt'] if row else 0


def get_estimated_wait_time(queue_id: int) -> int:
    """
    Get estimated wait time in minutes for a queue.

    Based on:
        service duration × waiting count
    """
    with get_cursor() as cursor:
        cursor.execute("""
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
        """, (queue_id,))

        row = cursor.fetchone()

        if row:
            return (
                row['estimated_duration_minutes']
                * row['waiting_count']
            )

        return 0

def create_customer_request(
    customer_id: int,
    queue_id: int
) -> int:
    """
    Create a draft request and add the documents configured
    for the service associated with the selected queue.
    """

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Verify the queue is active and get its service.
            cursor.execute("""
                SELECT q.queue_id, q.service_id
                FROM Queue q
                WHERE q.queue_id = %s
                  AND q.is_active = 1
            """, (queue_id,))

            queue = cursor.fetchone()

            if not queue:
                raise ValueError("Invalid or inactive queue")

            # Verify the customer exists.
            cursor.execute("""
                SELECT customer_id
                FROM Customer
                WHERE customer_id = %s
            """, (customer_id,))

            if not cursor.fetchone():
                raise ValueError("Customer not found")

            # Create the draft request.
            cursor.execute("""
                INSERT INTO customer_request (
                    customer_id,
                    queue_id,
                    request_status
                )
                VALUES (%s, %s, 'draft')
            """, (customer_id, queue_id))

            request_id = cursor.lastrowid

            # Add active documents configured for this service.
            cursor.execute("""
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
            """, (request_id, queue["service_id"]))

            return request_id

        finally:
            cursor.close()

def update_request_document_status(
    request_id: int,
    document_id: int,
    readiness_status: str
) -> None:
    """
    Update document readiness for a request.

    Allowed statuses: available, missing.
    """

    if readiness_status not in ("available", "missing"):
        raise ValueError(
            "Status must be 'available' or 'missing'"
        )

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Confirm that this document belongs to the
            # service associated with the request.
            cursor.execute("""
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
            """, (request_id, document_id))

            if not cursor.fetchone():
                raise ValueError(
                    "Document is not valid for this request"
                )

            # Update only the matching request-document row.
            cursor.execute("""
                UPDATE request_document
                SET readiness_status = %s,
                    confirmed_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                  AND document_id = %s
            """, (readiness_status, request_id, document_id))

        finally:
            cursor.close()

def confirm_customer_request_readiness(request_id: int) -> None:
    """Confirm readiness only when all required documents are available."""

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT request_status, readiness_confirmed
                FROM customer_request
                WHERE request_id = %s
                FOR UPDATE
            """, (request_id,))

            customer_request = cursor.fetchone()

            if not customer_request:
                raise ValueError("Customer request not found")

            if customer_request["request_status"] != "draft":
                raise ValueError(
                    "Only draft requests can be confirmed"
                )

            cursor.execute("""
                SELECT COUNT(*) AS incomplete_count
                FROM request_document rd
                JOIN customer_request cr
                    ON cr.request_id = rd.request_id
                JOIN Queue q
                    ON q.queue_id = cr.queue_id
                JOIN service_document sd
                    ON sd.service_id = q.service_id
                   AND sd.document_id = rd.document_id
                WHERE rd.request_id = %s
                  AND sd.is_required = 1
                  AND rd.readiness_status <> 'available'
            """, (request_id,))

            result = cursor.fetchone()

            if result["incomplete_count"] > 0:
                raise ValueError(
                    "All required documents must be available"
                )

            cursor.execute("""
                UPDATE customer_request
                SET readiness_confirmed = 1,
                    request_status = 'ready',
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
            """, (request_id,))

        finally:
            cursor.close()

def queue_customer_request(request_id: int) -> Dict[str, Any]:
    """
    Issue a queue token for a ready customer request and link it
    to the request in the same database transaction.
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)

        try:
            # Lock the request so simultaneous calls cannot queue it twice.
            cursor.execute("""
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
            """, (request_id,))

            customer_request = cursor.fetchone()

            if not customer_request:
                raise ValueError("Customer request not found")

            # If already queued, return the existing token.
            if customer_request["token_id"] is not None:
                cursor.execute("""
                    SELECT token_id, token_number, queue_id, customer_id
                    FROM Token
                    WHERE token_id = %s
                """, (customer_request["token_id"],))

                existing_token = cursor.fetchone()

                if existing_token:
                    return {
                        "request_id": request_id,
                        "token_id": existing_token["token_id"],
                        "token_number": existing_token["token_number"],
                        "queue_id": existing_token["queue_id"],
                        "request_status": customer_request["request_status"],
                        "message": "Request already has a token"
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

            # Ensure every required document is available.
            cursor.execute("""
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
            """, (request_id,))

            result = cursor.fetchone()

            if result["incomplete_count"] > 0:
                raise ValueError(
                    "All required documents must be available"
                )

            # Reuse the existing stored procedure on this connection.
            cursor.execute(
                "CALL issue_token(%s, %s, @p_token_id, @p_token_number)",
                (
                    customer_request["queue_id"],
                    customer_request["customer_id"]
                )
            )

            # Consume the procedure's result sets before further queries.
            cursor.fetchall()
            while cursor.nextset():
                pass

            cursor.execute("""
                SELECT
                    @p_token_id AS token_id,
                    @p_token_number AS token_number
            """)
            token = cursor.fetchone()

            if not token or token["token_id"] is None:
                raise RuntimeError("Token creation failed")

            # Link the token and change the request status.
            cursor.execute("""
                UPDATE customer_request
                SET token_id = %s,
                    request_status = 'queued',
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                  AND token_id IS NULL
                  AND request_status = 'ready'
            """, (token["token_id"], request_id))

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Could not link token to customer request"
                )

            return {
                "request_id": request_id,
                "token_id": token["token_id"],
                "token_number": token["token_number"],
                "queue_id": customer_request["queue_id"],
                "request_status": "queued",
                "message": "Customer request queued successfully"
            }

        finally:
            cursor.close()


create_customer_request

update_request_document_status

confirm_customer_request_readiness

