
"""QueueX Flask application factory."""

from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect, generate_csrf

from config import get_config


def create_app(config_name: str = None) -> Flask:
    """Create and configure the Flask application."""

    app = Flask(__name__)

    # Load configuration
    config = get_config(config_name)
    app.config.from_object(config)

    # Enable CSRF protection for state-changing requests.
    csrf = CSRFProtect()
    csrf.init_app(app)

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

    # Provide a CSRF token to browser clients.
    @app.route("/api/csrf-token", methods=["GET"])
    def csrf_token():
        return jsonify({"csrf_token": generate_csrf()})

    # Return a consistent response for CSRF validation failures.
    @app.errorhandler(400)
    def bad_request(error):
        description = getattr(error, "description", "Bad request")
        return jsonify({"error": description}), 400

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
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=app.config["DEBUG"],
    )
