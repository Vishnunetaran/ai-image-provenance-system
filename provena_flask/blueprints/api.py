"""
Main API blueprint for Provena.

Handles core image registration and verification endpoints.
Integrates: crypto, watermark, registry, and perceptual hashing services.
"""

from flask import Blueprint, jsonify, request, current_app
import cv2
import numpy as np
from datetime import datetime
import base64
import logging

from PIL import Image
import io as _io

from provena_flask.services.crypto_service import CryptoService
from provena_flask.services.watermark_service import WatermarkService
from provena_flask.services.registry_service import RegistryService
from provena_flask.services.phash_service import PerceptualHashService
from provena_flask.services.key_storage import KeyStorageService
from provena_flask.services import hydra_watermark, payload_codec


def _bgr_to_pil(bgr_image):
    """OpenCV BGR ndarray → PIL RGB Image."""
    rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _pil_to_bgr(pil_image):
    """PIL Image → OpenCV BGR ndarray (uint8)."""
    return cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)


def _coerce_native(obj):
    """Recursively convert numpy scalars to JSON-serializable Python types."""
    if isinstance(obj, dict):
        return {k: _coerce_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce_native(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _image_id_to_record_int(image_id: str) -> int:
    """Derive a stable 32-bit int from an image_id string. Used as the record_id
    embedded in the 6-byte hybrid watermark payload."""
    hex_part = image_id.replace('img-', '').replace('-', '')
    try:
        return int(hex_part, 16) % (2**32)
    except ValueError:
        # Fall back to a stable hash if the id isn't a UUID
        import hashlib
        return int.from_bytes(hashlib.sha256(image_id.encode()).digest()[:4], 'big')

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)


@api_bp.route('/status', methods=['GET'])
def api_status():
    """API status endpoint."""
    return jsonify({
        'status': 'operational',
        'version': 'v1',
        'service': 'provena-api'
    }), 200


@api_bp.route('/images/register', methods=['POST'])
def register_image():
    """
    Register an AI-generated image with provenance.
    
    Request JSON:
    {
        "image": "base64_encoded_image",
        "model_id": "gpt-vision-v1",
        "timestamp": "2026-01-26T13:00:00Z",
        "prompt_hash": "sha256:abc123..." (optional)
    }
    
    Response JSON:
    {
        "status": "success",
        "image_id": "img-abc123...",
        "watermarked_image": "base64_encoded_watermarked_image",
        "perceptual_hash": "phash:abc123...",
        "signature": "hex_signature",
        "public_key": "pem_encoded_public_key",
        "key_id": "key_identifier"
    }
    """
    try:
        # Validate request
        if not request.json:
            return jsonify({'error': 'Request must be JSON'}), 400
        
        data = request.json
        
        # Validate required fields
        required_fields = ['image', 'model_id', 'timestamp']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Decode image
        try:
            image_bytes = base64.b64decode(data['image'])
            image_array = np.frombuffer(image_bytes, dtype=np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            
            if image is None:
                return jsonify({'error': 'Invalid image data'}), 400
        except Exception as e:
            logger.error(f"Image decode error: {e}")
            return jsonify({'error': 'Failed to decode image'}), 400
        
        # Initialize services
        registry = RegistryService()
        phash_service = PerceptualHashService()
        key_storage = KeyStorageService(current_app.config['KEYS_DIR'])

        # Generate image ID
        image_id = registry.generate_image_id()

        # Compute perceptual hash on the ORIGINAL image (pre-watermark)
        perceptual_hash = phash_service.compute_phash(image)

        # Load or generate keys
        if not key_storage.key_exists('default'):
            logger.info("Generating default key pair")
            key_storage.generate_and_store_keys('default')

        private_key = key_storage.load_private_key('default')
        public_key = key_storage.load_public_key('default')
        key_id = key_storage.get_key_id('default')

        # Sign metadata
        metadata = {
            'image_id': image_id,
            'model_id': data['model_id'],
            'timestamp': data['timestamp'],
            'perceptual_hash': perceptual_hash,
        }
        if 'prompt_hash' in data:
            metadata['prompt_hash'] = data['prompt_hash']
        signature = CryptoService.sign(metadata, private_key)

        # Encode a 6-byte hybrid-watermark payload (4B record_id + 2B RS-ECC)
        record_id_int = _image_id_to_record_int(image_id)
        watermark_payload = payload_codec.encode(record_id_int)

        # Embed via HydraWatermark: neural (TrustMark) + DCT + spatial LSB.
        # First call lazy-loads the TrustMark model (~5s, ~700MB weights).
        try:
            pil_image = _bgr_to_pil(image)
            watermarked_pil = hydra_watermark.embed(pil_image, watermark_payload)
            watermarked_image = _pil_to_bgr(watermarked_pil)
        except Exception as e:
            logger.error(f"HydraWatermark embed failed: {e}", exc_info=True)
            return jsonify({
                'error': 'Watermark embedding failed',
                'message': str(e),
            }), 500

        # Encode watermarked image as PNG for the response
        _, buffer = cv2.imencode('.png', watermarked_image)
        watermarked_b64 = base64.b64encode(buffer).decode('utf-8')
        
        # Register in provenance database
        registration_result = registry.register_provenance(
            image_id=image_id,
            model_id=data['model_id'],
            timestamp=data['timestamp'],
            watermark_payload=watermark_payload,
            perceptual_hash=perceptual_hash,
            signature=signature,
            public_key=public_key,
            key_id=key_id,
            prompt_hash=data.get('prompt_hash')
        )
        
        if registration_result['status'] != 'success':
            return jsonify({
                'error': 'Failed to register provenance',
                'details': registration_result.get('message')
            }), 500
        
        logger.info(f"Successfully registered image: {image_id}")
        
        return jsonify({
            'status': 'success',
            'image_id': image_id,
            'watermarked_image': watermarked_b64,
            'perceptual_hash': perceptual_hash,
            'signature': signature.hex(),
            'public_key': public_key.decode('utf-8'),
            'key_id': key_id,
            'registered_at': datetime.utcnow().isoformat()
        }), 201
        
    except Exception as e:
        logger.error(f"Registration error: {e}", exc_info=True)
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@api_bp.route('/images/verify', methods=['POST'])
def verify_image():
    """
    Verify image provenance and integrity.
    
    Request JSON:
    {
        "image": "base64_encoded_image"
    }
    
    Response JSON:
    {
        "status": "verified" | "not_found" | "tampered",
        "image_id": "img-abc123...",
        "provenance": {...},
        "verification": {
            "watermark_extracted": true/false,
            "signature_valid": true/false,
            "perceptual_match": true/false
        }
    }
    """
    try:
        # Validate request
        if not request.json:
            return jsonify({'error': 'Request must be JSON'}), 400
        
        data = request.json
        
        if 'image' not in data:
            return jsonify({'error': 'Missing required field: image'}), 400
        
        # Decode image
        try:
            image_bytes = base64.b64decode(data['image'])
            image_array = np.frombuffer(image_bytes, dtype=np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            
            if image is None:
                return jsonify({'error': 'Invalid image data'}), 400
        except Exception as e:
            logger.error(f"Image decode error: {e}")
            return jsonify({'error': 'Failed to decode image'}), 400
        
        # Initialize services
        registry = RegistryService()
        phash_service = PerceptualHashService()

        # Try HydraWatermark extraction first. The neural (TrustMark) layer is
        # the robust one — it survives JPEG/resize/format conversion. The DCT
        # and LSB layers are diagnostic / break-glass.
        watermark_extracted = False
        watermark_breakdown = None
        provenance = None
        try:
            pil_image = _bgr_to_pil(image)
            vote_result = hydra_watermark.extract(pil_image)
            watermark_breakdown = _coerce_native(vote_result.breakdown)
            if vote_result.payload and len(vote_result.payload) == 6:
                # Try to find by exact payload match in the registry
                provenance = registry.search_by_watermark_payload(vote_result.payload)
                if provenance is not None:
                    watermark_extracted = True
        except Exception as e:
            logger.warning(f"HydraWatermark extract failed (continuing with pHash fallback): {e}")

        # Compute perceptual hash on the inspected image
        current_phash = phash_service.compute_phash(image)
        
        # If not found by watermark, try perceptual hash matching with
        # Hamming-distance tolerance. The watermark embedding perturbs the
        # luminance slightly, so a clean roundtrip's pHash is *close* to the
        # registered one, not identical.
        perceptual_match = False
        if not provenance:
            phash_tolerance = current_app.config.get('PHASH_VERIFY_DISTANCE', 12)
            matches = registry.search_by_perceptual_hash(current_phash, max_distance=phash_tolerance)
            if matches:
                provenance = matches[0]
                perceptual_match = True
        else:
            # Check if perceptual hash matches
            if provenance['perceptual_hash'] == current_phash:
                perceptual_match = True
            else:
                # Check Hamming distance
                distance = phash_service.hamming_distance(
                    current_phash,
                    provenance['perceptual_hash']
                )
                perceptual_match = distance <= 30  # Tolerant threshold
        
        if not provenance:
            return jsonify({
                'status': 'not_found',
                'message': 'No provenance record found for this image',
                'verification': {
                    'watermark_extracted': watermark_extracted,
                    'signature_valid': False,
                    'perceptual_match': False,
                    'watermark_layers': watermark_breakdown,
                }
            }), 404
        
        # Verify signature
        metadata = {
            'image_id': provenance['image_id'],
            'model_id': provenance['model_id'],
            'timestamp': provenance['timestamp'],
            'perceptual_hash': provenance['perceptual_hash']
        }
        
        if provenance.get('prompt_hash'):
            metadata['prompt_hash'] = provenance['prompt_hash']
        
        signature_valid = CryptoService.verify(
            metadata,
            provenance['signature'],
            provenance['public_key']
        )
        
        # Determine overall status
        if watermark_extracted and signature_valid and perceptual_match:
            status = 'verified'
        elif signature_valid and perceptual_match:
            status = 'verified_modified'  # Watermark lost but signature valid
        else:
            status = 'tampered'
        
        logger.info(f"Verification result: {status} for image_id: {provenance['image_id']}")
        
        return jsonify({
            'status': status,
            'image_id': provenance['image_id'],
            'provenance': {
                'model_id': provenance['model_id'],
                'timestamp': provenance['timestamp'],
                'prompt_hash': provenance.get('prompt_hash'),
                'key_id': provenance['key_id'],
                'created_at': provenance['created_at']
            },
            'verification': {
                'watermark_extracted': watermark_extracted,
                'signature_valid': signature_valid,
                'perceptual_match': perceptual_match,
                'watermark_layers': watermark_breakdown,
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Verification error: {e}", exc_info=True)
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@api_bp.route('/provenance/<image_id>', methods=['GET'])
def get_provenance(image_id):
    """
    Retrieve provenance record for an image.
    
    Response JSON:
    {
        "image_id": "img-abc123...",
        "model_id": "gpt-vision-v1",
        "timestamp": "2026-01-26T13:00:00Z",
        "perceptual_hash": "phash:abc123...",
        "signature": "hex_signature",
        "public_key": "pem_encoded_public_key",
        "key_id": "key_identifier",
        "created_at": "2026-01-26T13:00:01Z"
    }
    """
    try:
        registry = RegistryService()
        
        provenance = registry.get_provenance(image_id)
        
        if not provenance:
            return jsonify({
                'error': 'Not found',
                'message': f'No provenance record found for image_id: {image_id}'
            }), 404
        
        # Convert binary fields to readable format
        response = {
            'image_id': provenance['image_id'],
            'model_id': provenance['model_id'],
            'timestamp': provenance['timestamp'],
            'prompt_hash': provenance.get('prompt_hash'),
            'perceptual_hash': provenance['perceptual_hash'],
            'signature': provenance['signature'].hex(),
            'public_key': provenance['public_key'].decode('utf-8'),
            'key_id': provenance['key_id'],
            'created_at': provenance['created_at']
        }
        
        logger.info(f"Retrieved provenance for: {image_id}")
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"Provenance retrieval error: {e}", exc_info=True)
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@api_bp.route('/report/<image_id>', methods=['GET'])
def get_forensic_report(image_id):
    """
    Generate comprehensive forensic report for an image.
    
    Query Parameters:
        format: 'json' (default) or 'text'
    
    Response:
        JSON report with evidence summary, verdict, confidence score, and limitations
    """
    try:
        from provena_flask.services.forensic_service import ForensicReportService
        
        # Get format parameter
        report_format = request.args.get('format', 'json').lower()
        
        # Initialize forensic service
        forensic_service = ForensicReportService()
        
        # Generate report (without verification data - will use registry only)
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
