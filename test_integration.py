"""
End-to-End Integration Testing Suite.

Tests full pipeline: Register → Modify → Verify → Report
Validates consistency across all APIs.
"""

import os
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent))

os.environ['FLASK_ENV'] = 'testing'

from provena_flask import create_app
import base64


def create_test_image() -> np.ndarray:
    """Create test image."""
    image = np.zeros((256, 256, 3), dtype=np.uint8)
    for i in range(256):
        for j in range(256):
            image[i, j] = [int(255 * i / 256), int(255 * j / 256), 128]
    return image


def image_to_base64(image: np.ndarray) -> str:
    """Convert image to base64."""
    _, buffer = cv2.imencode('.png', image)
    return base64.b64encode(buffer).decode('utf-8')


def run_integration_tests():
    """Run end-to-end integration tests."""
    print("\n" + "="*70)
    print("  END-TO-END INTEGRATION TESTING SUITE")
    print("  Phase 8: Comprehensive Validation")
    print("="*70)
    
    app = create_app('testing')
    client = app.test_client()
    
    test_results = []
    
    # Test 1: Complete workflow
    print("\n[1/3] Testing complete workflow: Register → Verify → Report...")
    
    test_image = create_test_image()
    image_b64 = image_to_base64(test_image)
    
    # Step 1: Register
    print("  Step 1: Registering image...")
    register_response = client.post('/api/v1/images/register', json={
        'image': image_b64,
        'model_id': 'integration-test-model',
        'timestamp': '2026-01-26T15:20:00Z',
        'prompt_hash': 'sha256:integration_test'
    })
    
    if register_response.status_code != 201:
        print(f"  ✗ Registration failed: {register_response.status_code}")
        test_results.append(False)
    else:
        register_data = register_response.json()
        image_id = register_data['image_id']
        watermarked_b64 = register_data['watermarked_image']
        
        print(f"  ✓ Registered: {image_id}")
        
        # Step 2: Verify
        print("  Step 2: Verifying watermarked image...")
        verify_response = client.post('/api/v1/images/verify', json={
            'image': watermarked_b64
        })
        
        if verify_response.status_code != 200:
            print(f"  ✗ Verification failed: {verify_response.status_code}")
            test_results.append(False)
        else:
            verify_data = verify_response.json()
            print(f"  ✓ Verified: {verify_data['status']}")
            
            # Step 3: Get provenance
            print("  Step 3: Retrieving provenance...")
            prov_response = client.get(f'/api/v1/provenance/{image_id}')
            
            if prov_response.status_code != 200:
                print(f"  ✗ Provenance retrieval failed: {prov_response.status_code}")
                test_results.append(False)
            else:
                prov_data = prov_response.json()
                print(f"  ✓ Provenance retrieved: {prov_data['model_id']}")
                
                # Step 4: Generate report
                print("  Step 4: Generating forensic report...")
                report_response = client.get(f'/api/v1/report/{image_id}')
                
                if report_response.status_code != 200:
                    print(f"  ✗ Report generation failed: {report_response.status_code}")
                    test_results.append(False)
                else:
                    report_data = report_response.json()
                    print(f"  ✓ Report generated: {report_data['verdict']}")
                    
                    # Validate consistency
                    print("  Step 5: Validating consistency...")
                    consistency_checks = [
                        register_data['image_id'] == verify_data['image_id'],
                        verify_data['image_id'] == prov_data['image_id'],
                        prov_data['image_id'] == report_data['image_id'],
                        prov_data['model_id'] == 'integration-test-model',
                        verify_data['provenance']['model_id'] == 'integration-test-model'
                    ]
                    
                    all_consistent = all(consistency_checks)
                    test_results.append(all_consistent)
                    
                    status = "✓" if all_consistent else "✗"
                    print(f"  {status} Consistency: {sum(consistency_checks)}/{len(consistency_checks)} checks passed")
    
    # Test 2: API error handling
    print("\n[2/3] Testing API error handling...")
    
    error_tests = []
    
    # Missing required fields
    response = client.post('/api/v1/images/register', json={
        'image': image_b64
        # Missing model_id and timestamp
    })
    error_tests.append(response.status_code == 400)
    print(f"  Missing fields: {'✓' if error_tests[-1] else '✗'} Status: {response.status_code}")
    
    # Invalid image_id for provenance
    response = client.get('/api/v1/provenance/nonexistent-image-id')
    error_tests.append(response.status_code == 404)
    print(f"  Nonexistent ID: {'✓' if error_tests[-1] else '✗'} Status: {response.status_code}")
    
    # Invalid base64 image
    response = client.post('/api/v1/images/verify', json={
        'image': 'not-valid-base64!@#$'
    })
    error_tests.append(response.status_code in [400, 500])
    print(f"  Invalid base64: {'✓' if error_tests[-1] else '✗'} Status: {response.status_code}")
    
    error_handling_rate = sum(error_tests) / len(error_tests)
    test_results.append(error_handling_rate >= 0.8)
    print(f"  Error handling: {error_handling_rate*100:.1f}%")
    
    # Test 3: Data integrity across APIs
    print("\n[3/3] Testing data integrity across APIs...")
    
    # Register multiple images
    image_ids = []
    for i in range(3):
        response = client.post('/api/v1/images/register', json={
            'image': image_b64,
            'model_id': f'integrity-model-{i}',
            'timestamp': f'2026-01-26T15:21:{i:02d}Z'
        })
        
        if response.status_code == 201:
            image_ids.append(response.json()['image_id'])
    
    # Verify each can be retrieved
    integrity_checks = []
    for image_id in image_ids:
        prov_response = client.get(f'/api/v1/provenance/{image_id}')
        report_response = client.get(f'/api/v1/report/{image_id}')
        
        integrity_checks.append(
            prov_response.status_code == 200 and
            report_response.status_code == 200
        )
    
    integrity_rate = sum(integrity_checks) / len(integrity_checks) if integrity_checks else 0
    test_results.append(integrity_rate >= 0.9)
    print(f"  Data integrity: {integrity_rate*100:.1f}%")
    
    # Summary
    print("\n" + "="*70)
    print("  INTEGRATION TEST SUMMARY")
    print("="*70)
    
    success_rate = sum(test_results) / len(test_results)
    print(f"\nTests passed: {sum(test_results)}/{len(test_results)}")
    print(f"Success rate: {success_rate*100:.1f}%")
    print(f"Target: ≥90%")
    
    if success_rate >= 0.9:
        print("\n✓✓✓ INTEGRATION TESTS PASSED")
        return True
    else:
        print(f"\n⚠ INTEGRATION TESTS PARTIAL ({success_rate*100:.1f}%)")
        return success_rate >= 0.7


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
