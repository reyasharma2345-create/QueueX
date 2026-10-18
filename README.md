# QueueX: Advanced Banking Queue Management & Threat Containment System

QueueX is a comprehensive, production-grade queue management system designed for modern banking branches and customer service centers. It features a robust multi-role authentication system, real-time token tracking, dynamic document verification workflows, live display boards, and comprehensive database stored procedures ensuring transactional safety and race-condition immunity.

## Core Features
1. **Multi-Role Authentication & RBAC**: Separate portals and JWT/session security for Customers, Staff, and Administrators.
2. **Dynamic Service & Document Verification**: Customers select specific banking services, view required documents, confirm readiness, and receive sequenced queue tokens.
3. **Advanced Queue Engine**: Stored procedures (`sp_issue_token`, `sp_call_next_token`, etc.) with ACID compliance and transaction locking to prevent race conditions.
4. **Real-Time Display Board & Notifications**: Live branch display boards with real-time polling and automated customer notifications.
5. **Analytics & Performance Tracking**: Queue history, wait-time tracking, and staff performance metrics.

---

## Project Structure
```
QueueX/
├── app/
│   ├── __init__.py          # Flask application factory
│   ├── routes.py            # Main API & Web routes
│   ├── auth_routes.py       # Authentication & authorization endpoints
│   ├── utils/               # Database connectivity, security, and procedure wrappers
│   └── templates/           # Jinja2 HTML templates (customer, staff, display, counter)
├── db/                      # SQL schemas, seed data, and stored procedures
├── tests/                   # Comprehensive pytest test suites
├── requirements.txt         # Python dependencies
└── run.py                   # Application entry point
```

---

## Getting Started & Installation

### Prerequisites
- Python 3.10+
- MariaDB / MySQL Server

### 1. Setup Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and set your database credentials and secret keys:
```bash
cp .env.example .env
```

### 3. Initialize Database
Import the schema and stored procedures into your MariaDB instance:
```bash
mariadb -u root -p queuex < db/schema.sql
mariadb -u root -p queuex < db/procedures.sql
mariadb -u root -p queuex < db/seed.sql
```

### 4. Run Tests
Verify all system components with pytest:
```bash
export PYTHONPATH=.
pytest
```

### 5. Start the Application
```bash
python run.py
```
Access the application at `http://localhost:5000`.
