"""
Forensic Reports blueprint for Provena.

Generates comprehensive forensic reports for provenance verification.
"""

from flask import Blueprint, jsonify, request
import logging

from provena_flask.services.forensic_service import ForensicReportService

logger = logging.getLogger(__name__)

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/status', methods=['GET'])
def reports_status():
    """Reports service status endpoint."""
    return jsonify({
        'status': 'operational',
        'service': 'forensic-reports'
    }), 200


@reports_bp.route('/<image_id>', methods=['GET'])
def get_forensic_report(image_id):
    """
    Generate comprehensive forensic report for an image.
    
    Query Parameters:
        format: 'json' (default) or 'text'
    
    Response:
        JSON report or plain text report
    """
    try:
        # Get format parameter
        report_format = request.args.get('format', 'json').lower()
        
        # Initialize forensic service
        forensic_service = ForensicReportService()
        
        # Generate report
        report = forensic_service.generate_report(image_id)
        
        # Check if image was found
        if report.get('verdict') == 'not_found':
            return jsonify({
                'error': 'Not found',
                'message': f'No provenance record found for image_id: {image_id}',
                'image_id': image_id
            }), 404
        
        # Return in requested format
        if report_format == 'text':
            text_report = forensic_service.generate_human_readable_report(report)
            return text_report, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        else:
            return jsonify(report), 200
            
    except Exception as e:
        logger.error(f"Forensic report generation error for {image_id}: {e}", exc_info=True)
        return jsonify({
            'error': 'Internal server error',
            'message': str(e),
            'image_id': image_id
        }), 500

