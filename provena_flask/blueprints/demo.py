"""
Demo Web Interface blueprint for Provena-FLASK.

Provides a simple UI for demonstrating the system capabilities.
"""

from flask import Blueprint, render_template

demo_bp = Blueprint('demo', __name__, template_folder='../templates')


@demo_bp.route('/')
def index():
    """Render the demo interface."""
    return render_template('demo.html')
