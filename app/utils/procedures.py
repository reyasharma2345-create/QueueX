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