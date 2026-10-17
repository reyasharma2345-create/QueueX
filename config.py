
"""
QueueX Configuration Module
Centralized configuration management for the application.
"""

import os
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    """Base configuration class."""

    SECRET_KEY = os.getenv("SECRET_KEY")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.getenv("SESSION_COOKIE_SECURE", "0").strip() == "1"
    )
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    DEBUG = os.getenv("FLASK_DEBUG", "1").strip() == "1"

    # Database
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "queuex")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))

    # Application
    POLL_INTERVAL_SECONDS = int(
        os.getenv("POLL_INTERVAL_SECONDS", "3")
    )
    NO_SHOW_WINDOW_MINUTES = int(
        os.getenv("NO_SHOW_WINDOW_MINUTES", "5")
    )

    # Pagination
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "queuex-local-development-only"


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    DEBUG = True
    DB_NAME = os.getenv("TEST_DB_NAME", "queuex_test")
    SECRET_KEY = Config.SECRET_KEY or "queuex-test-only-secret"


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(config_name: str = None) -> Config:
    """Return the selected configuration, validating production secrets."""

    env = (
        config_name or os.getenv("FLASK_ENV", "development")
    ).strip().lower()

    config_class = config.get(env, config["default"])
    instance = config_class()

    if env == "production":
        secret = instance.SECRET_KEY

        if not secret or secret == "queuex-local-development-only":
            raise RuntimeError(
                "A strong SECRET_KEY environment variable is required "
                "in production."
            )

    return instance
