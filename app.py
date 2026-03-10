"""
app.py — PROVENA Trinity v2 Application Entry Point

Usage:
    # Development
    python app.py

    # Production (gunicorn)
    gunicorn -w 4 -b 0.0.0.0:5000 app:app

    # Using Flask CLI
    flask --app app:app run --debug
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Load .env if python-dotenv is available ────────────────────────────────
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[app] Loaded environment from {env_path}")
    else:
        env_example = Path(__file__).parent / ".env.example"
        if env_example.exists():
            load_dotenv(env_example)
            print(f"[app] No .env found — loaded defaults from .env.example")
except ImportError:
    pass   # python-dotenv not installed; rely on shell env

# ── Ensure the project root is on sys.path ─────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Create the Flask application ──────────────────────────────────────────
from provena_flask import create_app

_env = os.environ.get("FLASK_ENV", "development")
app = create_app(_env)


# ── Development runner ─────────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = _env == "development"

    print(f"\n🚀 PROVENA Trinity v2 API")
    print(f"   Environment : {_env}")
    print(f"   Trinity mode: {app.trinity.mode}")  # type: ignore[attr-defined]
    print(f"   Layers      : {app.trinity.available_layers}")  # type: ignore[attr-defined]
    print(f"   Listening   : http://{host}:{port}")
    print(f"   Health      : http://{host}:{port}/health\n")

    app.run(host=host, port=port, debug=debug)
