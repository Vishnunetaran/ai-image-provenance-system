"""
API endpoint tests for Phase 5.

Tests registration, verification, and provenance retrieval endpoints.
"""

import os
import sys
from pathlib import Path
import base64
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

# Set up test environment
os.environ['FLASK_ENV'] = 'testing'

from provena_flask import create_app


def create_test_image() -> np.ndarray:
    """Create a simple test image."""
    image = np.zeros((256, 256, 3), dtype=np.uint8)
    for i in range(256):
        for j in range(256):
            image[i, j] = [int(255 * i / 256), int(255 * j / 256), 128]
    return image


def image_to_base64(image: np.ndarray) -> str:
    """Convert image to base64 string."""
    _, buffer = cv2.imencode('.png', image)
    return base64.b64encode(buffer).decode('utf-8')


def run_tests():
    """Run API endpoint tests."""
    print("\n" + "="*70)
    print("  PROVENA API INTEGRATION TEST SUITE")
    print("  Phase 5: Flask API Endpoints")
    print("="*70)
    
    # Create Flask app
    app = create_app('testing')
    client = app.test_client()
    
    try:
        # Test 1: Health check
        print("\n[1/6] Testing health endpoint...")
        response = client.get('/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'healthy'
        print("✓ PASS: Health endpoint works")
        
        # Test 2: API status
        print("\n[2/6] Testing API status endpoint...")
        response = client.get('/api/v1/status')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'operational'
        print("✓ PASS: API status endpoint works")
        
        # Test 3: Register image
        print("\n[3/6] Testing image registration...")
        test_image = create_test_image()
        image_b64 = image_to_base64(test_image)
        
        register_data = {
            'image': image_b64,
            'model_id': 'test-model-v1',
            'timestamp': '2026-01-26T13:00:00Z',
            'prompt_hash': 'sha256:test123'
        }
        
        response = client.post('/api/v1/images/register', json=register_data)
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.get_json()}"
        
        data = response.get_json()
        assert data['status'] == 'success'
        assert 'image_id' in data
        assert 'watermarked_image' in data
        assert 'perceptual_hash' in data
        assert 'signature' in data
        
        image_id = data['image_id']
        watermarked_b64 = data['watermarked_image']
        
        print(f"   Image ID: {image_id}")
        print(f"   Perceptual hash: {data['perceptual_hash'][:16]}...")
        print("✓ PASS: Image registration works")
        
        # Test 4: Verify registered image
        print("\n[4/6] Testing image verification (watermarked)...")
        
        verify_data = {
            'image': watermarked_b64
        }
        
        response = client.post('/api/v1/images/verify', json=verify_data)
        assert response.status_code == 200
        
        data = response.get_json()
        assert data['status'] in ['verified', 'verified_modified']
        assert data['image_id'] == image_id
        assert data['verification']['signature_valid'] == True
        
        print(f"   Status: {data['status']}")
        print(f"   Watermark extracted: {data['verification']['watermark_extracted']}")
        print(f"   Signature valid: {data['verification']['signature_valid']}")
        print(f"   Perceptual match: {data['verification']['perceptual_match']}")
        print("✓ PASS: Image verification works")
        
        # Test 5: Get provenance
        print("\n[5/6] Testing provenance retrieval...")
        
        response = client.get(f'/api/v1/provenance/{image_id}')
        assert response.status_code == 200
        
        data = response.get_json()
        assert data['image_id'] == image_id
        assert data['model_id'] == 'test-model-v1'
        assert 'signature' in data
        assert 'public_key' in data
        
        print(f"   Model ID: {data['model_id']}")
        print(f"   Timestamp: {data['timestamp']}")
        print(f"   Key ID: {data['key_id']}")
        print("✓ PASS: Provenance retrieval works")
        
        # Test 6: Verify non-existent image
        print("\n[6/6] Testing verification of non-existent image...")
        
        unknown_image = create_test_image()
        # Modify to make it different
        unknown_image[100:150, 100:150] = [255, 0, 0]
        unknown_b64 = image_to_base64(unknown_image)
        
        verify_data = {
            'image': unknown_b64
        }
        
        response = client.post('/api/v1/images/verify', json=verify_data)
        assert response.status_code == 404
        
        data = response.get_json()
        assert data['status'] == 'not_found'
        
        print(f"   Status: {data['status']}")
        print("✓ PASS: Non-existent image correctly returns not_found")
        
        print("\n" + "="*70)
        print("  ✓✓✓ ALL API TESTS PASSED ✓✓✓")
        print("="*70)
        print("\nPhase 5 API Integration is VERIFIED and OPERATIONAL")
        print("\nEndpoints tested:")
        print("  - GET /health")
        print("  - GET /api/v1/status")
        print("  - POST /api/v1/images/register")
        print("  - POST /api/v1/images/verify")
        print("  - GET /api/v1/provenance/{image_id}")
        
        return True
        
    except AssertionError as e:
        print(f"\n✗ FAIL: {str(e)}")
        return False
    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
