"""
QueueX Route Blueprints

API and Web route definitions.
"""

from flask import Blueprint, request, jsonify, render_template, current_app
import re


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
)


from app.utils.db import execute_query, get_cursor


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


def _validate_token_staff(token_id):
    """
    Validate that a token exists and is assigned to
    an active staff member.

    Returns token/staff information or None.
    """

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
        LEFT JOIN Staff s
            ON t.staff_id = s.staff_id
        WHERE t.token_id = %s
        """,
        (token_id,),
        fetch=True
    )

    if not token:
        return None

    row = token[0]

    if not row['staff_id']:
        return None

    if row['staff_active'] != 1:
        return None

    return row


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

    return jsonify(q)


# ============================================================
# TOKEN APIs
# ============================================================

@api_bp.route('/tokens', methods=['POST'])
def create_token():
    """Issue a new token for a customer."""

    data = request.get_json() or {}

    queue_id = data.get('queue_id')
    customer_id = data.get('customer_id')

    if not queue_id or not customer_id:
        return _safe_error(
            'queue_id and customer_id required'
        )

    try:
        result = issue_token(
            queue_id,
            customer_id
        )

        return jsonify(result), 201

    except Exception:
        current_app.logger.exception(
            'Unable to create token'
        )

        return _safe_error(
            'Unable to create token'
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
    """Get token status and details."""

    token = get_token_status(token_id)

    if not token:
        return jsonify({
            'error': 'Token not found'
        }), 404

    return jsonify(token)


# ============================================================
# CALL SPECIFIC TOKEN
# ============================================================

@api_bp.route('/tokens/<int:token_id>/call', methods=['POST'])
def call_token(token_id: int):
    """Call a specific token."""

    data = request.get_json() or {}

    counter_id = data.get('counter_id')
    staff_id = data.get('staff_id')

    if not counter_id or not staff_id:
        return _safe_error(
            'counter_id and staff_id required'
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
                """,
                (
                    counter_id,
                    staff_id,
                    token_id
                )
            )

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
def call_next_token_endpoint(counter_id: int):
    """Call the next waiting token for a counter."""

    data = request.get_json() or {}

    staff_id = data.get('staff_id')

    if not staff_id:
        return _safe_error(
            'staff_id required'
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
def complete_token_endpoint(token_id: int):
    """Mark a token as completed."""

    # Verify token has an active assigned staff member
    token = _validate_token_staff(token_id)

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
def serve_token_endpoint(token_id: int):
    """Mark a called token as serving."""

    # Verify token has an active assigned staff member
    token = _validate_token_staff(token_id)

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
def no_show_token(token_id: int):
    """Mark a called token as no-show."""

    # Verify token has an active assigned staff member
    token = _validate_token_staff(token_id)

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


@api_bp.route('/branches', methods=['GET'])
def list_branches():
    """List all branches."""

    rows = execute_query(
        "SELECT * FROM Branch ORDER BY name"
    )

    return jsonify(rows)


@api_bp.route('/services', methods=['GET'])
def list_services():
    """List all services."""

    rows = execute_query(
        "SELECT * FROM Service ORDER BY name"
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
# CUSTOMER REQUEST APIs
# ============================================================

@api_bp.route('/customer-requests', methods=['POST'])
def create_customer_request_api():
    """Create a draft request for an existing customer."""

    data = request.get_json(silent=True) or {}

    try:
        customer_id = int(data.get('customer_id'))
        queue_id = int(data.get('queue_id'))

        if customer_id <= 0 or queue_id <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return _safe_error(
            'Valid customer_id and queue_id are required'
        )

    try:
        request_id = create_customer_request(
            customer_id=customer_id,
            queue_id=queue_id
        )

        return jsonify({
            'request_id': request_id,
            'customer_id': customer_id,
            'queue_id': queue_id,
            'request_status': 'draft',
            'message': 'Customer request created successfully'
        }), 201

    except ValueError as error:
        return _safe_error(str(error), 400)

    except Exception:
        current_app.logger.exception(
            'Unable to create customer request'
        )
        return _safe_error(
            'Unable to create customer request',
            500
        )


@api_bp.route(
    '/customer-requests/<int:request_id>',
    methods=['GET']
)
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
