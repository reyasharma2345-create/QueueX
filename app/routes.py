"""
QueueX Route Blueprints

API and Web route definitions.
"""
from mysql.connector import Error as MySQLError
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    current_app,
    session,
)
from app.utils.auth import require_permission, require_customer_permission
import re
from functools import wraps
from app.utils.procedures import (
    issue_token,
    call_next_token,
    complete_token,
    mark_no_show,
    serve_token,
    get_token_status,
    get_queue_waiting_count,
    get_estimated_wait_time,
    create_customer_and_token,
    create_customer_request,
    update_request_document_status,
    confirm_customer_request_readiness,
    queue_customer_request,
)


from app.utils.db import execute_query, get_cursor

def require_own_customer_request(view_function):
    """Ensure the authenticated customer owns the requested record."""
    @wraps(view_function)
    def wrapped(request_id, *args, **kwargs):
        account = session.get("account") or {}
        customer_id = account.get("customer_id")

        try:
            rows = execute_query(
                """
                SELECT request_id
                FROM customer_request
                WHERE request_id = %s
                  AND customer_id = %s
                LIMIT 1
                """,
                (request_id, customer_id),
                fetch=True,
            )
        except Exception:
            current_app.logger.exception(
                "Unable to verify customer request ownership"
            )
            return _safe_error(
                "Unable to verify request ownership",
                500,
            )

        if not rows:
            return _safe_error("Request not found", 404)

        return view_function(request_id, *args, **kwargs)

    return wrapped


# ============================================================
# API BLUEPRINT
# ============================================================

api_bp = Blueprint('api', __name__)


def _safe_error(message: str, status_code: int = 400):
    """Return a safe API error without exposing internal details."""
    return jsonify({
        'error': message
    }), status_code


def _validate_customer_input(full_name, phone, email):
    """Validate customer information before database operations."""

    if not isinstance(full_name, str) or not full_name.strip():
        return 'full_name required'

    if not isinstance(phone, str) or not phone.strip():
        return 'phone required'

    full_name = full_name.strip()
    phone = phone.strip()

    if len(full_name) > 100:
        return 'full_name is too long'

    if not re.fullmatch(r'\+?[0-9]{10,15}', phone):
        return 'Invalid phone number'

    if email is not None:
        if not isinstance(email, str):
            return 'Invalid email'

        email = email.strip()

        if len(email) > 254:
            return 'Invalid email'

        if not re.fullmatch(
            r'^[^\s@]+@[^\s@]+\.[^\s@]+$',
            email
        ):
            return 'Invalid email'

    return None


# ============================================================
# BRANCHES
# ============================================================

@api_bp.route('/branches', methods=['GET'])
def get_branches():
    """Return active branches for the customer workflow."""
    try:
        branches = execute_query(
    """
    SELECT branch_id, name, address, phone
    FROM Branch
    ORDER BY name
    """,
    fetch=True,
)
        return jsonify(branches or []), 200
    except Exception:
        current_app.logger.exception('GET /api/branches failed')
        return _safe_error('Unable to fetch branches', 500)


# ============================================================
# SERVICES
# ============================================================


@api_bp.route('/services', methods=['GET'])
def get_services():
    """Return active services for the customer workflow."""
    try:
        services = execute_query(
            """
            SELECT service_id, name, description, estimated_duration_minutes
            FROM Service
            ORDER BY name
            """,
            fetch=True,
        )
        return jsonify(services or []), 200
    except Exception:
        current_app.logger.exception('GET /api/services failed')
        return _safe_error('Unable to fetch services', 500)
@api_bp.route('/services/<int:service_id>/documents', methods=['GET'])
def get_service_documents(service_id: int):
    """Get documents required for a specific service."""
    try:
        documents = execute_query(
            """SELECT d.document_id, d.name, d.description, sd.is_required
               FROM service_document sd
               JOIN document d ON d.document_id = sd.document_id
               WHERE sd.service_id = %s
                 AND d.is_active = 1
               ORDER BY d.name""",
            (service_id,),
            fetch=True
        )
        return jsonify(documents), 200
    except Exception:
        current_app.logger.exception('Unable to fetch service documents')
        return _safe_error('Unable to fetch service documents', 500)


# ============================================================
# QUEUES FOR BRANCH
# ============================================================

@api_bp.route('/branches/<int:branch_id>/queues', methods=['GET'])
def get_branch_queues(branch_id: int):
    """Get active queues for a branch."""
    try:
        queues = execute_query(
            """SELECT q.queue_id, q.name, q.branch_id, q.service_id, q.is_active,
                      s.name AS service_name, s.estimated_duration_minutes
               FROM Queue q
               JOIN Service s ON s.service_id = q.service_id
               WHERE q.branch_id = %s
                 AND q.is_active = TRUE
               ORDER BY q.name""",
            (branch_id,),
            fetch=True
        )
        return jsonify(queues), 200
    except Exception:
        current_app.logger.exception('Unable to fetch branch queues')
        return _safe_error('Unable to fetch branch queues', 500)


# ============================================================
# SECURITY HELPERS
# ============================================================

def _validate_active_staff(staff_id):
    """
    Validate that a staff member exists and is active.
    Returns the staff row or None.
    """

    try:
        staff_id = int(staff_id)
    except (TypeError, ValueError):
        return None

    staff = execute_query(
        """
        SELECT
            staff_id,
            full_name,
            role,
            branch_id,
            is_active
        FROM Staff
        WHERE staff_id = %s
          AND is_active = TRUE
        """,
        (staff_id,),
        fetch=True
    )

    if not staff:
        return None

    return staff[0]


def _validate_staff_for_counter(staff_id, counter_id):
    """
    Validate that:
    - staff exists
    - staff is active
    - counter exists
    - counter is active
    - staff and counter belong to the same branch

    Returns counter/staff information when valid.
    """

    try:
        staff_id = int(staff_id)
        counter_id = int(counter_id)
    except (TypeError, ValueError):
        return None

    result = execute_query(
        """
        SELECT
            s.staff_id,
            s.full_name,
            s.role,
            s.branch_id AS staff_branch_id,
            c.counter_id,
            c.branch_id AS counter_branch_id,
            c.queue_id,
            c.is_active AS counter_active
        FROM Staff s
        JOIN Counter c
            ON s.branch_id = c.branch_id
        WHERE s.staff_id = %s
          AND s.is_active = TRUE
          AND c.counter_id = %s
          AND c.is_active = TRUE
        """,
        (staff_id, counter_id),
        fetch=True
    )

    if not result:
        return None

    row = result[0]

    if row['staff_branch_id'] != row['counter_branch_id']:
        return None

    return row


def _validate_token_staff(token_id, current_staff_id):
    """
    Validate that the token is assigned to the currently
    authenticated, active staff member at an active counter
    in the same branch.

    Returns token/staff information or None.
    """
    try:
        token_id = int(token_id)
        current_staff_id = int(current_staff_id)
    except (TypeError, ValueError):
        return None

    if token_id <= 0 or current_staff_id <= 0:
        return None

    token = execute_query(
        """
        SELECT
            t.token_id,
            t.queue_id,
            t.status,
            t.counter_id,
            t.staff_id,
            s.full_name AS staff_name,
            s.is_active AS staff_active
        FROM Token t
        JOIN Staff s
            ON t.staff_id = s.staff_id
        JOIN Counter c
            ON t.counter_id = c.counter_id
        WHERE t.token_id = %s
          AND t.staff_id = %s
          AND s.is_active = TRUE
          AND c.is_active = TRUE
          AND c.branch_id = s.branch_id
          AND c.queue_id = t.queue_id
        """,
        (token_id, current_staff_id),
        fetch=True,
    )

    return token[0] if token else None


# ============================================================
# GLOBAL API ERROR HANDLER
# ============================================================

@api_bp.errorhandler(Exception)
def handle_api_error(error):
    """
    Catch unexpected API errors and prevent internal
    database/application details from being exposed.
    """

    current_app.logger.exception('Unhandled API error')

    return jsonify({
        'error': 'Internal server error'
    }), 500


# ============================================================
# QUEUE APIs
# ============================================================

@api_bp.route('/queues', methods=['GET'])
def list_queues():
    """List all active queues with waiting counts and estimated wait times."""

    rows = execute_query(
        """
        SELECT
            q.queue_id,
            q.name,
            q.is_active,
            q.last_token_number,
            b.name AS branch_name,
            s.name AS service_name,
            s.estimated_duration_minutes,

            (
                SELECT COUNT(*)
                FROM Token
                WHERE queue_id = q.queue_id
                  AND status = 'waiting'
            ) AS waiting_count

        FROM Queue q

        JOIN Branch b
            ON q.branch_id = b.branch_id

        JOIN Service s
            ON q.service_id = s.service_id

        WHERE q.is_active = TRUE

        ORDER BY b.name, s.name
        """
    )

    for row in rows:
        row['estimated_wait_minutes'] = get_estimated_wait_time(
            row['queue_id']
        )

    return jsonify(rows)


@api_bp.route('/queues/<int:queue_id>', methods=['GET'])
def get_queue(queue_id: int):
    """
    Get queue details with waiting count,
    estimated wait time, and current tokens.
    """

    queue = execute_query(
        """
        SELECT
            q.*,
            b.name AS branch_name,
            s.name AS service_name,
            s.estimated_duration_minutes

        FROM Queue q

        JOIN Branch b
            ON q.branch_id = b.branch_id

        JOIN Service s
            ON q.service_id = s.service_id

        WHERE q.queue_id = %s
        """,
        (queue_id,),
        fetch=True
    )

    if not queue:
        return jsonify({
            'error': 'Queue not found'
        }), 404

    q = queue[0]

    q['waiting_count'] = get_queue_waiting_count(
        queue_id
    )

    q['estimated_wait_minutes'] = get_estimated_wait_time(
        queue_id
    )

    tokens = execute_query(
    """
    SELECT
        t.token_number,
        t.status,
        t.called_at,
        c.full_name AS customer_name,
        t.counter_id,
        t.token_id,
        t.queue_id,
        q.name AS queue_name,
        s.name AS service_name
    FROM Token t
    JOIN Customer c
        ON t.customer_id = c.customer_id
    JOIN Queue q
        ON t.queue_id = q.queue_id
    JOIN Service s
        ON q.service_id = s.service_id
    WHERE t.queue_id = %s
      AND t.status IN ('waiting', 'called', 'serving')
    ORDER BY t.token_number
    LIMIT 20
    """,
    (queue_id,)
)

    q['tokens'] = tokens

    return jsonify(q)


# ============================================================
# TOKEN APIs
# ============================================================


@api_bp.route('/tokens', methods=['POST'])
def create_token():
    """Issue a new token for a customer."""

    data = request.get_json(silent=True) or {}

    queue_id = data.get('queue_id')
    customer_id = data.get('customer_id')

    # Validate that both IDs are positive integers.
    try:
        if isinstance(queue_id, bool) or isinstance(customer_id, bool):
            raise ValueError

        queue_id = int(queue_id)
        customer_id = int(customer_id)

        if queue_id <= 0 or customer_id <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return _safe_error(
            'Valid queue_id and customer_id are required',
            400
        )

    try:
        result = issue_token(
            queue_id,
            customer_id
        )

        return jsonify(result), 201

    except MySQLError as exc:
        # These errors represent expected invalid-input rejections:
        # 1644: SIGNAL raised by the stored procedure.
        # 1452: Foreign-key violation, e.g. nonexistent customer.
        if getattr(exc, 'errno', None) in (1644, 1452):
            current_app.logger.warning(
                'Token creation rejected (database error %s)',
                exc.errno
            )

            return _safe_error(
                'Unable to create token',
                400
            )

        # Other database failures are server errors, not bad requests.
        current_app.logger.exception(
            'Database error while creating token'
        )

        return _safe_error(
            'Unable to create token',
            500
        )

    except Exception:
        current_app.logger.exception(
            'Unexpected error creating token'
        )

        return _safe_error(
            'Unable to create token',
            500
        )



# ============================================================
# ATOMIC CUSTOMER + TOKEN API
# ============================================================

@api_bp.route('/customer-token', methods=['POST'])
def create_customer_token():
    """
    Create customer and issue token in one database transaction.
    """

    data = request.get_json() or {}

    full_name = data.get('full_name')
    phone = data.get('phone')

    raw_email = data.get('email')

    email = (
        raw_email.strip()
        if isinstance(raw_email, str) and raw_email.strip()
        else None
    )

    queue_id = data.get('queue_id')

    validation_error = _validate_customer_input(
        full_name,
        phone,
        email
    )

    if validation_error:
        return _safe_error(validation_error)

    if not queue_id:
        return _safe_error(
            'queue_id required'
        )

    full_name = full_name.strip()
    phone = phone.strip()

    try:
        queue_id = int(queue_id)

    except (TypeError, ValueError):
        return _safe_error(
            'Invalid queue_id'
        )

    try:
        result = create_customer_and_token(
            full_name=full_name,
            phone=phone,
            email=email,
            queue_id=queue_id
        )

        return jsonify(result), 201

    except Exception:
        current_app.logger.exception(
            'Unable to create customer token'
        )

        return _safe_error(
            'Unable to create customer token'
        )


# ============================================================
# TOKEN STATUS
# ============================================================

@api_bp.route('/tokens/<int:token_id>', methods=['GET'])
def get_token(token_id: int):
    """Return token details only to its owner or authorized staff."""
    account = session.get("account")

    if not isinstance(account, dict):
        return jsonify({"error": "Authentication required"}), 401

    role = account.get("role_name")

    if role == "customer":
        # Revalidate the customer account and its active status.
        from app.utils.auth import get_current_session_customer

        account_id = account.get("account_id")
        customer = get_current_session_customer(account_id)

        if customer is None:
            session.clear()
            return jsonify({"error": "Authentication required"}), 401

        token_rows = execute_query(
            """
            SELECT token_id, customer_id, status
            FROM Token
            WHERE token_id = %s
            LIMIT 1
            """,
            (token_id,),
            fetch=True,
        )

        if not token_rows:
            return jsonify({"error": "Token not found"}), 404

        token_row = token_rows[0]

        if token_row["customer_id"] != customer["customer_id"]:
            return jsonify({"error": "Token not found"}), 404

        # Only expose fields required by the customer-facing UI.
        token = get_token_status(token_id)

        if not token:
            return jsonify({"error": "Token not found"}), 404

        return jsonify({
            "status": token["status"],
            "waiting_ahead": token.get("waiting_ahead"),
            "estimated_wait_minutes": token.get("estimated_wait_minutes"),
        })

    if role in ("admin", "staff"):
        # Reuse the existing database-backed staff permission check.
        from app.utils.auth import get_current_session_account, get_user_permissions

        account_id = account.get("account_id")
        current_account = get_current_session_account(account_id)

        if current_account is None:
            session.clear()
            return jsonify({"error": "Authentication required"}), 401

        if "queue_operate" not in get_user_permissions(account_id):
            return jsonify({"error": "Permission denied"}), 403

        token = get_token_status(token_id)

        if not token:
            return jsonify({"error": "Token not found"}), 404

        return jsonify(token)

    return jsonify({"error": "Authentication required"}), 401

# ============================================================
# CALL SPECIFIC TOKEN
# ============================================================

@api_bp.route('/tokens/<int:token_id>/call', methods=['POST'])
@require_permission("queue_operate")
def call_token(token_id: int):
    """Call a specific token."""
    data = request.get_json(silent=True) or {}

    counter_id = data.get('counter_id')
    account = session.get('account', {})
    staff_id = account.get('staff_id') if isinstance(account, dict) else None
    if not counter_id or not staff_id:
      return _safe_error(
        'counter_id required'
    )




    # Validate staff + counter
    staff_counter = _validate_staff_for_counter(
        staff_id,
        counter_id
    )

    if not staff_counter:
        return _safe_error(
            'Staff member is not authorized for this counter',
            403
        )

    token = get_token_status(token_id)

    if not token:
        return jsonify({
            'error': 'Token not found'
        }), 404

    if token['status'] != 'waiting':
        return jsonify({
            'error': (
                'Token is not in waiting status '
                '(current status cannot be changed)'
            )
        }), 400

    # Verify counter belongs to token queue
    counter_queue = execute_query(
        """
        SELECT
            queue_id,
            branch_id,
            is_active
        FROM Counter
        WHERE counter_id = %s
        """,
        (counter_id,),
        fetch=True
    )

    if not counter_queue:
        return _safe_error(
            'Counter not found',
            404
        )

    if not counter_queue[0]['is_active']:
        return _safe_error(
            'Counter is not active',
            400
        )

    if counter_queue[0]['queue_id'] != token['queue_id']:
        return _safe_error(
            'Token not in this counter queue'
        )

    try:
        from app.utils.db import get_cursor

        with get_cursor() as cursor:

            cursor.execute(
                """
                UPDATE Token
                SET
                    status = 'called',
                    counter_id = %s,
                    staff_id = %s,
                    called_at = CURRENT_TIMESTAMP
                WHERE token_id = %s
                AND status = 'waiting'
                """,
                (
                    counter_id,
                    staff_id,
                    token_id
                )
            )
            if cursor.rowcount != 1:
                return jsonify({
                    "error": (
                        "Token is not in waiting status "
                        "(current status cannot be changed)"
                    )
                }), 400


            cursor.execute(
                """
                INSERT INTO Queue_History (
                    token_id,
                    previous_status,
                    new_status,
                    changed_by
                )
                VALUES (
                    %s,
                    'waiting',
                    'called',
                    %s
                )
                """,
                (
                    token_id,
                    staff_id
                )
            )

        token = get_token_status(token_id)

        return jsonify(token)

    except Exception:
        current_app.logger.exception(
            'Unable to call token'
        )

        return _safe_error(
            'Unable to call token'
        )


# ============================================================
# CALL NEXT TOKEN
# ============================================================

@api_bp.route(
    '/counters/<int:counter_id>/call-next',
    methods=['POST']
)

@require_permission("queue_operate")

def call_next_token_endpoint(counter_id: int):
    """Call the next waiting token for a counter."""
    account = session.get('account', {})
    staff_id = account.get('staff_id') if isinstance(account, dict) else None

    if not staff_id:
     return _safe_error(
        'Authentication required',
        401
    )



    # Validate staff + counter before calling next token
    staff_counter = _validate_staff_for_counter(
        staff_id,
        counter_id
    )

    if not staff_counter:
        return _safe_error(
            'Staff member is not authorized for this counter',
            403
        )

    try:
        result = call_next_token(
            counter_id,
            staff_id
        )

        if not result:
            return jsonify({
                'error': 'No waiting tokens'
            }), 404

        # Send notification to customer
        send_token_called_notification(result['token_id'])

        return jsonify(result)

    except Exception:
        current_app.logger.exception(
            'Unable to call next token'
        )

        return _safe_error(
            'Unable to call next token'
        )


# ============================================================
# COMPLETE TOKEN
# ============================================================

@api_bp.route(
    '/tokens/<int:token_id>/complete',
    methods=['POST']
)
@require_permission("queue_operate")

def complete_token_endpoint(token_id: int):
    """Mark a token as completed."""

    # Verify token has an active assigned staff member
    account = session.get("account", {})
    staff_id = (
        account.get("staff_id")
        if isinstance(account, dict)
        else None
    )

    if not staff_id:
        return _safe_error("Authentication required", 401)

    token = _validate_token_staff(token_id, staff_id)

    if not token:
        return _safe_error(
            'Token is not assigned to an active staff member',
            403
        )

    if token['status'] != 'serving':
        return _safe_error(
            'Only a serving token can be completed'
        )

    try:
        complete_token(token_id)

        # Send notification to customer
        send_token_completed_notification(token_id)

        return jsonify({
            'success': True
        })

    except Exception:
        current_app.logger.exception(
            'Unable to complete token'
        )

        return _safe_error(
            'Unable to complete token'
        )


# ============================================================
# SERVE TOKEN
# ============================================================

@api_bp.route(
    '/tokens/<int:token_id>/serve',
    methods=['POST']
)
@require_permission("queue_operate")
def serve_token_endpoint(token_id: int):
    """Mark a called token as serving."""

    # Verify token has an active assigned staff member
    account = session.get("account", {})
    staff_id = (
        account.get("staff_id")
        if isinstance(account, dict)
        else None
    )

    if not staff_id:
        return _safe_error("Authentication required", 401)

    token = _validate_token_staff(token_id, staff_id)

    if not token:
        return _safe_error(
            'Token is not assigned to an active staff member',
            403
        )

    if token['status'] != 'called':
        return _safe_error(
            'Only a called token can be moved to serving'
        )

    try:
        serve_token(token_id)

        # Send notification to customer
        send_token_serving_notification(token_id)

        return jsonify({
            'success': True
        })

    except Exception:
        current_app.logger.exception(
            'Unable to serve token'
        )

        return _safe_error(
            'Unable to serve token'
        )


# ============================================================
# NO-SHOW TOKEN
# ============================================================

@api_bp.route(
    '/tokens/<int:token_id>/no-show',
    methods=['POST']
)
@require_permission("queue_operate")
def no_show_token(token_id: int):
    """Mark a called token as no-show."""

    # Verify token has an active assigned staff member
    account = session.get("account", {})
    staff_id = (
        account.get("staff_id")
        if isinstance(account, dict)
        else None
    )

    if not staff_id:
        return _safe_error("Authentication required", 401)

    token = _validate_token_staff(token_id, staff_id)

    if not token:
        return _safe_error(
            'Token is not assigned to an active staff member',
            403
        )

    if token['status'] != 'called':
        return _safe_error(
            'Only a called token can be marked as no-show'
        )

    data = request.get_json() or {}

    window_minutes = data.get(
        'window_minutes',
        current_app.config.get(
            'NO_SHOW_WINDOW_MINUTES',
            5
        )
    )

    try:
        window_minutes = int(window_minutes)

        if window_minutes < 0:
            return _safe_error(
                'Invalid window_minutes'
            )

        success = mark_no_show(
            token_id,
            window_minutes
        )

        if not success:
            return _safe_error(
                'No-show window not elapsed'
            )

        return jsonify({
            'success': True
        })

    except (TypeError, ValueError):
        return _safe_error(
            'Invalid window_minutes'
        )

    except Exception:
        current_app.logger.exception(
            'Unable to mark token as no-show'
        )

        return _safe_error(
            'Unable to mark token as no-show'
        )


# ============================================================
# COUNTER / STAFF / BRANCH / SERVICE APIs
# ============================================================

@api_bp.route('/counters', methods=['GET'])
def list_counters():
    """List all counters with their queue and branch."""

    rows = execute_query(
        """
        SELECT
            c.counter_id,
            c.name,
            c.location,
            c.floor,
            c.is_active,
            q.name AS queue_name,
            b.name AS branch_name

        FROM Counter c

        JOIN Queue q
            ON c.queue_id = q.queue_id

        JOIN Branch b
            ON c.branch_id = b.branch_id

        WHERE c.is_active = TRUE

        ORDER BY b.name, q.name, c.name
        """
    )

    return jsonify(rows)


@api_bp.route('/staff', methods=['GET'])
def list_staff():
    """
    List active staff members without exposing
    login/contact details through this API.
    """

    rows = execute_query(
        """
        SELECT
            s.staff_id,
            s.full_name,
            s.role,
            s.is_active,
            b.name AS branch_name

        FROM Staff s

        JOIN Branch b
            ON s.branch_id = b.branch_id

        WHERE s.is_active = TRUE

        ORDER BY b.name, s.full_name
        """
    )

    return jsonify(rows)


# ============================================================
# CUSTOMER APIs
# ============================================================

@api_bp.route('/customers', methods=['GET'])
def list_customers():
    """
    List customers without exposing phone/email
    through the public API.
    """

    rows = execute_query(
        """
        SELECT
            customer_id,
            full_name
        FROM Customer
        ORDER BY full_name
        """
    )

    return jsonify(rows)


@api_bp.route('/customers', methods=['POST'])
def create_customer():
    """Create a new customer."""

    data = request.get_json() or {}

    full_name = data.get('full_name')
    phone = data.get('phone')

    raw_email = data.get('email')

    email = (
        raw_email.strip()
        if isinstance(raw_email, str) and raw_email.strip()
        else None
    )

    validation_error = _validate_customer_input(
        full_name,
        phone,
        email
    )

    if validation_error:
        return _safe_error(validation_error)

    full_name = full_name.strip()
    phone = phone.strip()

    try:
        with current_app.app_context():

            from app.utils.db import get_cursor

            with get_cursor() as cursor:

                cursor.execute(
                    """
                    INSERT INTO Customer (
                        full_name,
                        phone,
                        email
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (
                        full_name,
                        phone,
                        email
                    )
                )

                customer_id = cursor.lastrowid

        return jsonify({
            'customer_id': customer_id,
            'full_name': full_name
        }), 201

    except Exception:
        current_app.logger.exception(
            'Unable to create customer'
        )

        return _safe_error(
            'Unable to create customer'
        )


# ============================================================
# WEB BLUEPRINT
# ============================================================

web_bp = Blueprint('web', __name__)


# ============================================================
# DASHBOARD
# ============================================================

@web_bp.route('/')
def index():
    """Main dashboard."""

    return render_template(
        'index.html'
    )


# ============================================================
# CUSTOMER PAGE
# ============================================================

@web_bp.route('/customer')
def customer_page():
    """Customer token generation page."""

    return render_template(
        'customer.html'
    )


# ============================================================
# QUEUE PAGE
# ============================================================

@web_bp.route('/queue/<int:queue_id>')
def queue_view(queue_id: int):
    """Public queue display."""

    queue = execute_query(
        """
        SELECT
            q.*,
            b.name AS branch_name,
            s.name AS service_name,
            s.estimated_duration_minutes

        FROM Queue q

        JOIN Branch b
            ON q.branch_id = b.branch_id

        JOIN Service s
            ON q.service_id = s.service_id

        WHERE q.queue_id = %s
        """,
        (queue_id,),
        fetch=True
    )

    if not queue:
        return "Queue not found", 404

    q = queue[0]

    q['waiting_count'] = get_queue_waiting_count(
        queue_id
    )

    q['estimated_wait_minutes'] = get_estimated_wait_time(
        queue_id
    )

    tokens = execute_query(
        """
        SELECT
            t.token_number,
            t.status,
            t.called_at,
            c.full_name AS customer_name,
            t.counter_id,
            t.token_id

        FROM Token t

        JOIN Customer c
            ON t.customer_id = c.customer_id

        WHERE t.queue_id = %s
          AND t.status IN ('waiting', 'called', 'serving')

        ORDER BY t.token_number

        LIMIT 20
        """,
        (queue_id,)
    )

    q['tokens'] = tokens

    return render_template(
        'queue.html',
        queue=q
    )


# ============================================================
# COUNTER PAGE
# ============================================================

@web_bp.route('/counter/<int:counter_id>')
def counter_view(counter_id: int):
    """Staff counter view."""

    counter = execute_query(
        """
        SELECT
            c.*,
            q.name AS queue_name,
            b.name AS branch_name

        FROM Counter c

        JOIN Queue q
            ON c.queue_id = q.queue_id

        JOIN Branch b
            ON c.branch_id = b.branch_id

        WHERE c.counter_id = %s
        """,
        (counter_id,),
        fetch=True
    )

    if not counter:
        return "Counter not found", 404

    return render_template(
        'counter.html',
        counter=counter[0]
    )


# ============================================================
# COUNTERS LIST
# ============================================================

@web_bp.route('/counters')
def counters_list():
    """List all counters with their queue and branch."""

    counters = execute_query(
        """
        SELECT
            c.counter_id,
            c.name,
            c.location,
            c.floor,
            c.is_active,
            q.name AS queue_name,
            b.name AS branch_name

        FROM Counter c

        JOIN Queue q
            ON c.queue_id = q.queue_id

        JOIN Branch b
            ON c.branch_id = b.branch_id

        WHERE c.is_active = TRUE

        ORDER BY b.name, q.name, c.name
        """
    )

    return render_template(
        'counters.html',
        counters=counters
    )


# ============================================================
# STAFF LIST
# ============================================================

@web_bp.route('/staff')
def staff_list():
    """List all staff members."""

    staff = execute_query(
        """
        SELECT
            s.staff_id,
            s.username,
            s.full_name,
            s.email,
            s.role,
            s.is_active,
            b.name AS branch_name

        FROM Staff s

        JOIN Branch b
            ON s.branch_id = b.branch_id

        WHERE s.is_active = TRUE

        ORDER BY b.name, s.full_name
        """
    )

    return render_template(
        'staff.html',
        staff=staff
    )


# ============================================================
# BRANCHES LIST
# ============================================================

@web_bp.route('/branches')
def branches_list():
    """List all branches."""

    branches = execute_query(
        "SELECT * FROM Branch ORDER BY name"
    )

    return render_template(
        'branches.html',
        branches=branches
    )


# ============================================================
# SERVICES LIST
# ============================================================

@web_bp.route('/services')
def services_list():
    """List all services."""

    services = execute_query(
        "SELECT * FROM Service ORDER BY name"
    )

    return render_template(
        'services.html',
        services=services
    )


# ============================================================
# DISPLAY BOARD
# ============================================================

@web_bp.route('/display')
def display_board():
    """Public display board for branch queues."""
    return render_template('display.html')


# ============================================================
# STAFF COUNTER APIs
# ============================================================

@api_bp.route('/staff/counters', methods=['GET'])
@require_permission("queue_operate")
def get_staff_counters():
    """Get counters assigned to the logged-in staff member's branch."""
    account = session.get('account', {})
    staff_id = account.get('staff_id') if isinstance(account, dict) else None
    
    if not staff_id:
        return _safe_error('Authentication required', 401)
    
    try:
        # Get staff's branch_id
        staff_info = execute_query(
            """SELECT branch_id FROM Staff WHERE staff_id = %s AND is_active = TRUE""",
            (staff_id,),
            fetch=True
        )
        
        if not staff_info:
            return _safe_error('Staff not found or inactive', 403)
        
        branch_id = staff_info[0]['branch_id']
        
        # Get counters for this branch
        counters = execute_query(
            """SELECT c.counter_id, c.name, c.location, c.floor, c.is_active,
                      q.name AS queue_name, q.queue_id,
                      b.name AS branch_name
               FROM Counter c
               JOIN Queue q ON c.queue_id = q.queue_id
               JOIN Branch b ON c.branch_id = b.branch_id
               WHERE c.branch_id = %s
                 AND c.is_active = TRUE
                 AND q.is_active = TRUE
               ORDER BY q.name, c.name""",
            (branch_id,),
            fetch=True
        )
        
        return jsonify(counters), 200
        
    except Exception:
        current_app.logger.exception('Unable to fetch staff counters')
        return _safe_error('Unable to fetch staff counters', 500)


@api_bp.route('/counters/<int:counter_id>/call-specific', methods=['POST'])
@require_permission("queue_operate")
def call_specific_token_endpoint(counter_id: int):
    """Call a specific token by number for a counter."""
    account = session.get('account', {})
    staff_id = account.get('staff_id') if isinstance(account, dict) else None
    
    if not staff_id:
        return _safe_error('Authentication required', 401)
    
    data = request.get_json(silent=True) or {}
    token_number = data.get('token_number')
    
    try:
        token_number = int(token_number)
        if token_number <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _safe_error('Valid token_number is required')
    
    # Validate staff + counter
    staff_counter = _validate_staff_for_counter(staff_id, counter_id)
    
    if not staff_counter:
        return _safe_error('Staff member is not authorized for this counter', 403)
    
    try:
        from app.utils.procedures import call_specific_token
        token = call_specific_token(counter_id, staff_id, token_number)
        
        if not token:
            return jsonify({'error': 'Token not found or not in waiting status'}), 404
            
        # Send notification to customer
        send_token_called_notification(token['token_id'])
        
        return jsonify(token)
        
    except Exception:
        current_app.logger.exception('Unable to call specific token')
        return _safe_error('Unable to call specific token')


# ============================================================
# TOKEN REISSUE
# ============================================================

@api_bp.route('/tokens/<int:token_id>/reissue', methods=['POST'])
@require_permission("queue_operate")
def reissue_token_endpoint(token_id: int):
    """Reissue a token - put it back in waiting queue."""
    # Verify token has an active assigned staff member
    account = session.get("account", {})
    staff_id = (
        account.get("staff_id")
        if isinstance(account, dict)
        else None
    )

    if not staff_id:
        return _safe_error("Authentication required", 401)

    token = _validate_token_staff(token_id, staff_id)
    
    if not token:
        return _safe_error('Token is not assigned to an active staff member', 403)
    
    if token['status'] not in ('called', 'serving'):
        return _safe_error('Only called or serving tokens can be reissued')
    
    try:
        from app.utils.procedures import reissue_token
        reissue_token(token_id)
        
        return jsonify({'success': True})
        
    except Exception:
        current_app.logger.exception('Unable to reissue token')
        return _safe_error('Unable to reissue token')

# ============================================================
# CUSTOMER REQUEST APIs
# ============================================================


@api_bp.route('/customer-requests', methods=['POST'])
@require_customer_permission("customer:manage_own")
def create_customer_request_api():
    """Create a draft request for the authenticated customer."""
    data = request.get_json(silent=True) or {}

    # Derive customer identity from the verified session, not client input.
    account = session.get("account") or {}
    customer_id = account.get("customer_id")

    try:
        customer_id = int(customer_id)
        queue_id = int(data.get("queue_id"))

        if customer_id <= 0 or queue_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _safe_error("A valid queue_id is required")

    try:
        request_id = create_customer_request(
            customer_id=customer_id,
            queue_id=queue_id,
        )

        return jsonify({
            "request_id": request_id,
            "customer_id": customer_id,
            "queue_id": queue_id,
            "request_status": "draft",
            "message": "Customer request created successfully",
        }), 201

    except ValueError as error:
        return _safe_error(str(error), 400)

    except Exception:
        current_app.logger.exception(
            "Unable to create customer request"
        )
        return _safe_error(
            "Unable to create customer request",
            500,
        )


@api_bp.route(
    '/customer-requests/<int:request_id>',
    methods=['GET']
)
@require_customer_permission("customer:read")
@require_own_customer_request
def get_customer_request_api(request_id):
    """Return request details and configured document statuses."""

    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    cr.request_id,
                    cr.customer_id,
                    cr.queue_id,
                    cr.token_id,
                    cr.request_status,
                    cr.readiness_confirmed,
                    cr.created_at,
                    cr.updated_at,
                    q.name AS queue_name,
                    s.name AS service_name
                FROM customer_request cr
                JOIN Queue q
                    ON q.queue_id = cr.queue_id
                JOIN Service s
                    ON s.service_id = q.service_id
                WHERE cr.request_id = %s
            """, (request_id,))

            customer_request = cursor.fetchone()

            if not customer_request:
                return _safe_error('Request not found', 404)

            cursor.execute("""
                SELECT
                    d.document_id,
                    d.name AS document_name,
                    d.description,
                    sd.is_required,
                    rd.readiness_status,
                    rd.confirmed_at
                FROM request_document rd
                JOIN document d
                    ON d.document_id = rd.document_id
                JOIN customer_request cr
                    ON cr.request_id = rd.request_id
                JOIN Queue q
                    ON q.queue_id = cr.queue_id
                JOIN service_document sd
                    ON sd.service_id = q.service_id
                   AND sd.document_id = d.document_id
                WHERE rd.request_id = %s
                  AND d.is_active = 1
                ORDER BY d.name
            """, (request_id,))

            documents = cursor.fetchall()

        return jsonify({
            'request': customer_request,
            'documents': documents
        }), 200

    except Exception:
        current_app.logger.exception(
            'Unable to retrieve customer request'
        )
        return _safe_error(
            'Unable to retrieve customer request',
            500
        )


@api_bp.route(
    '/customer-requests/<int:request_id>/documents',
    methods=['PATCH']
)
@require_customer_permission("customer:manage_own")
@require_own_customer_request
def update_customer_request_document_api(request_id):
    """Update the readiness status of a request document."""

    data = request.get_json(silent=True) or {}

    try:
        document_id = int(data.get('document_id'))

        if document_id <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return _safe_error('A valid document_id is required')

    readiness_status = data.get('readiness_status')

    if readiness_status not in ('available', 'missing'):
        return _safe_error(
            "readiness_status must be 'available' or 'missing'"
        )

    try:
        update_request_document_status(
            request_id=request_id,
            document_id=document_id,
            readiness_status=readiness_status
        )

        return jsonify({
            'request_id': request_id,
            'document_id': document_id,
            'readiness_status': readiness_status,
            'message': 'Document readiness updated'
        }), 200

    except ValueError as error:
        return _safe_error(str(error), 400)

    except Exception:
        current_app.logger.exception(
            'Unable to update document readiness'
        )
        return _safe_error(
            'Unable to update document readiness',
            500
        )

@api_bp.route(
    '/customer-requests/<int:request_id>/confirm-readiness',
    methods=['POST']
)
@require_customer_permission("customer:manage_own")
@require_own_customer_request
def confirm_customer_request_readiness_api(request_id):
    """Confirm that all required documents are available."""
    try:
        confirm_customer_request_readiness(request_id)

        return jsonify({
            'request_id': request_id,
            'request_status': 'ready',
            'readiness_confirmed': True,
            'message': 'Request readiness confirmed'
        }), 200

    except ValueError as error:
        return _safe_error(str(error), 400)

    except Exception:
        current_app.logger.exception(
            'Unable to confirm request readiness'
        )
        return _safe_error(
            'Unable to confirm request readiness',
            500
        )

@api_bp.route(
    '/customer-requests/<int:request_id>/queue',
    methods=['POST']
)
@require_customer_permission("customer:manage_own")
@require_own_customer_request
def queue_customer_request_api(request_id):
    """Issue a token for a ready customer request."""
    try:
        result = queue_customer_request(request_id)
        return jsonify(result), 200

    except ValueError as error:
        return _safe_error(str(error), 400)

    except Exception:
        current_app.logger.exception(
            'Unable to queue customer request'
        )
        return _safe_error(
            'Unable to queue customer request',
            500
        )


# ============================================================
# DISPLAY BOARD APIs
# ============================================================

@api_bp.route('/display/branch', methods=['GET'])
def get_display_branch():
    """Get display data for all active queues at the branch."""
    # For now, use the first active branch. In production, this would be
    # determined by the display device configuration.
    branch = execute_query(
       """SELECT branch_id, name FROM Branch ORDER BY branch_id LIMIT 1""",
        fetch=True
    )
    
    if not branch:
        return _safe_error('No active branch found', 404)
    
    branch_id = branch[0]['branch_id']
    branch_name = branch[0]['name']
    
    # Get all active queues for this branch with their current state
    queues_data = execute_query(
        """SELECT q.queue_id, q.name, q.service_id, s.name AS service_name, q.branch_id
           FROM Queue q
           JOIN Service s ON q.service_id = s.service_id
           WHERE q.branch_id = %s AND q.is_active = TRUE
           ORDER BY q.queue_id""",
        (branch_id,),
        fetch=True
    )
    
    queues = []
    for q in queues_data:
        # Get current token (called or serving)
        current_token = execute_query(
            """SELECT t.token_id, t.token_number, t.status, t.counter_id, c.name AS counter_name
               FROM Token t
               LEFT JOIN Counter c ON t.counter_id = c.counter_id
               WHERE t.queue_id = %s AND t.status IN ('called', 'serving')
               ORDER BY 
                   CASE t.status WHEN 'serving' THEN 1 WHEN 'called' THEN 2 END,
                   t.called_at ASC
               LIMIT 1""",
            (q['queue_id'],),
            fetch=True
        )
        
        # Get waiting tokens (up to 10)
        waiting_tokens = execute_query(
            """SELECT t.token_id, t.token_number, t.status, t.counter_id, c.name AS counter_name
               FROM Token t
               LEFT JOIN Counter c ON t.counter_id = c.counter_id
               WHERE t.queue_id = %s AND t.status = 'waiting'
               ORDER BY t.token_number ASC
               LIMIT 10""",
            (q['queue_id'],),
            fetch=True
        )
        
        queues.append({
            'queue_id': q['queue_id'],
            'name': q['name'],
            'service_name': q['service_name'],
            'current_token': current_token[0] if current_token else None,
            'waiting_tokens': waiting_tokens if waiting_tokens else []
        })
    
    return jsonify({
        'branch_id': branch_id,
        'branch_name': branch_name,
        'queues': queues
    })


@api_bp.route('/display/branch/<int:branch_id>', methods=['GET'])
def get_display_branch_by_id(branch_id: int):
    """Get display data for a specific branch."""
    branch = execute_query(
        """SELECT branch_id, name FROM Branch WHERE branch_id = %s""",
        (branch_id,),
        fetch=True
    )
    
    if not branch:
        return _safe_error('Branch not found', 404)
    
    branch_name = branch[0]['name']
    
    # Get all active queues for this branch
    queues_data = execute_query(
        """SELECT q.queue_id, q.name, q.service_id, s.name AS service_name
           FROM Queue q
           JOIN Service s ON q.service_id = s.service_id
           WHERE q.branch_id = %s AND q.is_active = TRUE
           ORDER BY q.queue_id""",
        (branch_id,),
        fetch=True
    )
    
    queues = []
    for q in queues_data:
        current_token = execute_query(
            """SELECT t.token_id, t.token_number, t.status, t.counter_id, c.name AS counter_name
               FROM Token t
               LEFT JOIN Counter c ON t.counter_id = c.counter_id
               WHERE t.queue_id = %s AND t.status IN ('called', 'serving')
               ORDER BY 
                   CASE t.status WHEN 'serving' THEN 1 WHEN 'called' THEN 2 END,
                   t.called_at ASC
               LIMIT 1""",
            (q['queue_id'],),
            fetch=True
        )
        
        waiting_tokens = execute_query(
            """SELECT t.token_id, t.token_number, t.status, t.counter_id, c.name AS counter_name
               FROM Token t
               LEFT JOIN Counter c ON t.counter_id = c.counter_id
               WHERE t.queue_id = %s AND t.status = 'waiting'
               ORDER BY t.token_number ASC
               LIMIT 10""",
            (q['queue_id'],),
            fetch=True
        )
        
        queues.append({
            'queue_id': q['queue_id'],
            'name': q['name'],
            'service_name': q['service_name'],
            'current_token': current_token[0] if current_token else None,
            'waiting_tokens': waiting_tokens if waiting_tokens else []
        })
    
    return jsonify({
        'branch_id': branch_id,
        'branch_name': branch_name,
        'queues': queues
    })


# ============================================================
# NOTIFICATION APIs
# ============================================================

@api_bp.route('/notifications', methods=['GET'])
@require_customer_permission("customer:read")
def get_customer_notifications():
    """Get notifications for the current customer."""
    account = session.get('account', {})
    customer_id = account.get('customer_id') if isinstance(account, dict) else None
    
    if not customer_id:
        return _safe_error('Authentication required', 401)
    
    notifications = execute_query(
        """SELECT notification_id, token_id, channel, message, status, sent_at, created_at
           FROM Notification
           WHERE customer_id = %s
           ORDER BY created_at DESC
           LIMIT 50""",
        (customer_id,),
        fetch=True
    )
    
    return jsonify({'notifications': notifications if notifications else []})


@api_bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
@require_customer_permission("customer:manage_own")
def mark_notification_read(notification_id: int):
    """Mark a notification as read (update status)."""
    account = session.get('account', {})
    customer_id = account.get('customer_id') if isinstance(account, dict) else None
    
    if not customer_id:
        return _safe_error('Authentication required', 401)
    
    result = execute_query(
        """UPDATE Notification
           SET status = 'sent'
           WHERE notification_id = %s AND customer_id = %s""",
        (notification_id, customer_id),
        fetch=False
    )
    
    if result == 0:
        return _safe_error('Notification not found', 404)
    
    return jsonify({'success': True, 'message': 'Notification marked as read'})


def create_notification(customer_id: int, token_id: int, channel: str, message: str):
    """Create a new notification."""
    with get_cursor() as cursor:
        cursor.execute(
            """INSERT INTO Notification (customer_id, token_id, channel, message, status)
               VALUES (%s, %s, %s, %s, 'pending')""",
            (customer_id, token_id, channel, message)
        )
        return cursor.lastrowid


def send_token_called_notification(token_id: int):
    """Send notification when a token is called."""
    token = get_token_status(token_id)
    if not token:
        return
    
    customer_id = token['customer_id']
    token_number = token['token_number']
    counter_name = token.get('counter_name', '')
    queue_name = token.get('queue_name', '')
    
    message = f'Your token {token_number} has been called'
    if counter_name:
        message += f' at Counter {counter_name}'
    if queue_name:
        message += f' for {queue_name}'
    message += '. Please proceed to the counter.'
    
    # Create in-app notification
    create_notification(customer_id, token_id, 'in_app', message)
    
    # TODO: Add SMS/email/push notification integrations here


def send_token_serving_notification(token_id: int):
    """Send notification when service starts."""
    token = get_token_status(token_id)
    if not token:
        return
    
    customer_id = token['customer_id']
    token_number = token['token_number']
    counter_name = token.get('counter_name', '')
    
    message = f'Service started for token {token_number}'
    if counter_name:
        message += f' at Counter {counter_name}'
    message += '.'
    
    create_notification(customer_id, token_id, 'in_app', message)


def send_token_completed_notification(token_id: int):
    """Send notification when service is completed."""
    token = get_token_status(token_id)
    if not token:
        return
    
    customer_id = token['customer_id']
    token_number = token['token_number']
    
    message = f'Your service for token {token_number} has been completed. Thank you for visiting!'
    
    create_notification(customer_id, token_id, 'in_app', message)
