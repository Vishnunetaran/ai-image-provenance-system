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
    debug_mode = config_name == 'development' and os.environ.get('PROVENA_DEBUG', '0') == '1'
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

    # Pre-warm the HydraWatermark stack so the first user request is fast.
    # First TrustMark inference can take ~2-3 min (model load + JIT); doing it
    # at startup makes the demo feel instant once /health returns.
    if os.environ.get('PROVENA_PREWARM', '1') == '1':
        try:
            import numpy as np
            from PIL import Image
            from provena_flask.services import hydra_watermark, payload_codec
            print("  · pre-warming HydraWatermark (TrustMark model load) …")
            dummy = Image.fromarray(np.zeros((256, 256, 3), dtype=np.uint8) + 128)
            hydra_watermark.embed(dummy, payload_codec.encode(0))
            print("  · pre-warm done; the first /register call will be fast.\n")
        except Exception as e:
            print(f"  · pre-warm failed (server will still start): {e}\n")

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug_mode,
        threaded=True,
        use_reloader=False,
    )
