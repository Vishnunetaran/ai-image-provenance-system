"""
Application entry point for Provena.

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
    # Default to 5001 — port 5000 is reserved by macOS AirPlay Receiver.
    port = int(os.environ.get('PORT', '5001'))

    print(f"""
    ╔═══════════════════════════════════════════════════════╗
    ║   PROVENA · Cryptographic provenance for AI images    ║
    ║   Environment: {config_name:<39}║
    ║   Debug Mode:  {str(debug_mode):<39}║
    ║   URL:         http://localhost:{port:<22} ║
    ╚═══════════════════════════════════════════════════════╝
    """)

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug_mode
    )
