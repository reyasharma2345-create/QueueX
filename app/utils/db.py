"""
Database connection utility for QueueX.
Supports both MySQL and PostgreSQL via environment configuration.
"""
import os
from contextlib import contextmanager
from typing import Generator, Optional

import mysql.connector
from mysql.connector import pooling
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursorDict

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


class DatabaseConfig:
    """Database configuration from environment variables."""
    
    def __init__(self):
        self.host = os.getenv('DB_HOST', 'localhost')
        self.port = int(os.getenv('DB_PORT', '3306'))
        self.user = os.getenv('DB_USER', 'root')
        self.password = os.getenv('DB_PASSWORD', '')
        self.database = os.getenv('DB_NAME', 'queuex')
        self.pool_size = int(os.getenv('DB_POOL_SIZE', '5'))
    
    @property
    def connection_params(self) -> dict:
        return {
            'host': self.host,
            'port': self.port,
            'user': self.user,
            'password': self.password,
            'database': self.database,
            'charset': 'utf8mb4',
            'autocommit': False,
            'raise_on_warnings': True,
        }


# Global connection pool (initialized lazily)
_connection_pool: Optional[pooling.MySQLConnectionPool] = None
_config = DatabaseConfig()


def get_connection_pool() -> pooling.MySQLConnectionPool:
    """Get or create the global connection pool."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = pooling.MySQLConnectionPool(
            pool_name="queuex_pool",
            pool_size=_config.pool_size,
            **_config.connection_params
        )
    return _connection_pool


def get_db_connection() -> MySQLConnection:
    """Get a connection from the pool."""
    pool = get_connection_pool()
    return pool.get_connection()


@contextmanager
def get_db() -> Generator[MySQLConnection, None, None]:
    """
    Context manager for database connections.
    Automatically commits on success, rolls back on exception, and returns connection to pool.
    
    Usage:
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM Queue")
            rows = cursor.fetchall()
    """
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(dictionary: bool = True) -> Generator[MySQLCursorDict, None, None]:
    """
    Context manager for database cursors.
    Automatically closes cursor after use.
    
    Usage:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM Queue")
            rows = cursor.fetchall()
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=dictionary)
        try:
            yield cursor
        finally:
            cursor.close()


def execute_query(query: str, params: tuple = None, dictionary: bool = True, fetch: bool = True):
    """
    Execute a query and return results.
    
    Args:
        query: SQL query string with %s placeholders
        params: Query parameters tuple
        dictionary: Return rows as dictionaries (default True)
        fetch: Whether to fetch results (False for INSERT/UPDATE/DELETE)
    
    Returns:
        List of rows (if fetch=True) or None
    """
    with get_cursor(dictionary=dictionary) as cursor:
        cursor.execute(query, params or ())
        if fetch:
            return cursor.fetchall()
        return None


def execute_many(query: str, params_list: list, dictionary: bool = True):
    """
    Execute a query multiple times with different parameters.
    
    Args:
        query: SQL query string with %s placeholders
        params_list: List of parameter tuples
        dictionary: Return rows as dictionaries
    
    Returns:
        Number of affected rows
    """
    with get_db() as conn:
        cursor = conn.cursor(dictionary=dictionary)
        try:
            cursor.executemany(query, params_list)
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()


def test_connection() -> bool:
    """Test database connectivity. Returns True if successful."""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


def init_database() -> bool:
    """
    Initialize database by running schema.sql.
    Returns True if successful.
    """
    import subprocess
    import sys
    
    schema_path = os.path.join(os.path.dirname(__file__), '..', '..', 'db', 'schema.sql')
    schema_path = os.path.abspath(schema_path)
    
    if not os.path.exists(schema_path):
        print(f"Schema file not found: {schema_path}")
        return False
    
    try:
        # Use mysql command line for schema initialization
        cmd = [
            'mysql',
            '-h', _config.host,
            '-P', str(_config.port),
            '-u', _config.user,
            f'-p{_config.password}' if _config.password else '',
            _config.database
        ]
        
        with open(schema_path, 'r') as f:
            result = subprocess.run(cmd, stdin=f, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("Database schema initialized successfully")
            return True
        else:
            print(f"Schema initialization failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("Schema initialization timed out")
        return False
    except FileNotFoundError:
        print("MySQL client not found. Please run schema.sql manually:")
        print(f"  mysql -h {_config.host} -P {_config.port} -u {_config.user} -p {_config.database} < {schema_path}")
        return False
    except Exception as e:
        print(f"Schema initialization error: {e}")
        return False


if __name__ == '__main__':
    # Quick connection test when run directly
    print("Testing database connection...")
    if test_connection():
        print("✓ Database connection successful")
    else:
        print("✗ Database connection failed")
        sys.exit(1)