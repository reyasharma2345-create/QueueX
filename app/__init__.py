
"""QueueX Flask application factory."""

from flask import Flask

from config import get_config


def create_app(config_name: str = None) -> Flask:
    """Create and configure the Flask application."""

    app = Flask(__name__)

    # Load configuration
    config = get_config(config_name)
    app.config.from_object(config)

    # Initialize database and check connectivity
    from app.utils.db import test_connection

    with app.app_context():
        if test_connection():
            app.logger.info("Database connected")
        else:
            app.logger.error("Database connection failed")

    # Register blueprints
    from app.routes import api_bp, web_bp
    from app.auth_routes import auth_bp

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp)

    # Health check endpoint
    @app.route("/health")
    def health():
        return {"status": "ok", "service": "queuex"}

    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return {"error": "Not found"}, 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled application error")
        return {"error": "Internal server error"}, 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
