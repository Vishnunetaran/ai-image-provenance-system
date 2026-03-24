"""
Application entry point for Provena-FLASK.

Run this file to start the development server.
"""

import os
from provena_flask import create_app

# Determine environment from environment variable
config_name = os.environ.get('FLASK_ENV', 'development')

# Create Flask application
app = create_app(config_name)

if __name__ == '__main__':
    # Development server settings
    debug_mode = config_name == 'development'
    
    print(f"""
    ╔═══════════════════════════════════════════════════════╗
    ║   Provena-FLASK: AI Image Provenance Service         ║
    ║   Environment: {config_name:<38} ║
    ║   Debug Mode: {str(debug_mode):<39} ║
    ╚═══════════════════════════════════════════════════════╝
    """)
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=debug_mode,
        use_reloader=False
    )
