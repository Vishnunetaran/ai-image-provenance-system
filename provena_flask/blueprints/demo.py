"""
Demo Web Interface blueprint for Provena-FLASK.

Provides a simple UI for demonstrating the system capabilities.
"""

from flask import Blueprint, render_template
from provena_flask.auth import create_key_record

demo_bp = Blueprint('demo', __name__, template_folder='../templates')


@demo_bp.route('/')
def index():
    """Render the demo interface."""
    record = create_key_record(org_name="Demo UI Temp Key", tier="free")
    demo_key = record["plaintext_key"]
    return render_template('demo.html', demo_key=demo_key)

