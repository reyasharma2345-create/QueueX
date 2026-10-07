"""
QueueX Flask Application Factory
"""
from flask import Flask
from config import get_config


def create_app(config_name: str = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        config_name: Configuration name (development, production, testing)
                    Defaults to FLASK_ENV environment variable.
        
    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)
    
    # Load configuration
    config = get_config() if config_name is None else get_config()
    if config_name:
        from config import config as config_dict
        config = config_dict.get(config_name, config_dict['default'])()
    app.config.from_object(config)
    
    # Initialize database
    from app.utils.db import init_database, test_connection
    
    with app.app_context():
        # Test database connection
        if test_connection():
            print("✓ Database connected")
        else:
            print("✗ Database connection failed")
            print("  Run schema.sql and seed.sql manually if needed")
    
    # Register blueprints
    from app.routes import api_bp, web_bp
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(web_bp)
    
    # Health check endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'queuex'}
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return {'error': 'Not found'}, 404
    
    @app.errorhandler(500)
    def server_error(e):
        return {'error': 'Internal server error'}, 500
    
    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)