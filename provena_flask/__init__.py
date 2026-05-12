"""
Provena: AI Image Provenance & Forensic Verification Service

Flask application factory for the provenance verification system.
"""

import os
import logging
from flask import Flask


def create_app(config_name='development'):
    """
    Application factory pattern for Flask app.
    
    Args:
        config_name: Configuration environment ('development', 'production', 'testing')
    
    Returns:
        Flask application instance
    """
    app = Flask(__name__)
    
    # Load configuration
    from provena_flask.config import config
    app.config.from_object(config[config_name])
    
    # Initialize logging
    setup_logging(app)
    
    # Register blueprints
    register_blueprints(app)
    
    # Health check route
    @app.route('/health')
    def health_check():
        return {'status': 'healthy', 'service': 'provena-flask'}, 200
    
    app.logger.info(f"Provena initialized in {config_name} mode")
    
    return app


def setup_logging(app):
    """Configure application logging."""
    log_level = app.config.get('LOG_LEVEL', 'INFO')
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def register_blueprints(app):
    """Register all application blueprints."""
    from provena_flask.blueprints.api import api_bp
    from provena_flask.blueprints.registry import registry_bp
    from provena_flask.blueprints.watermark import watermark_bp
    from provena_flask.blueprints.verification import verification_bp
    from provena_flask.blueprints.reports import reports_bp
    from provena_flask.blueprints.demo import demo_bp
    
    # API blueprints
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(registry_bp, url_prefix='/registry')
    app.register_blueprint(watermark_bp, url_prefix='/watermark')
    app.register_blueprint(verification_bp, url_prefix='/verification')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    
    # Demo UI blueprint
    app.register_blueprint(demo_bp)
    
    app.logger.info("All blueprints registered successfully")
