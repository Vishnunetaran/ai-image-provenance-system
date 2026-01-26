"""
Registry blueprint for Provena-FLASK.

Handles provenance registry operations.
"""

from flask import Blueprint, jsonify

registry_bp = Blueprint('registry', __name__)


@registry_bp.route('/status', methods=['GET'])
def registry_status():
    """Registry status endpoint."""
    return jsonify({
        'status': 'operational',
        'service': 'provenance-registry'
    }), 200


# Placeholder - will be implemented in Phase 2
@registry_bp.route('/records', methods=['GET'])
def list_records():
    """
    List all provenance records.
    
    TODO: Implement in Phase 2
    """
    return jsonify({
        'error': 'Not implemented',
        'message': 'Registry listing will be implemented in Phase 2'
    }), 501
