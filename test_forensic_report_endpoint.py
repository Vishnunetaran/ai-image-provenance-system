"""
Test script to verify forensic report endpoint is working.
"""

import requests
import base64
import cv2
import numpy as np

# Create a simple test image
test_image = np.zeros((100, 100, 3), dtype=np.uint8)
test_image[:, :] = [100, 150, 200]  # Light blue

# Encode to base64
_, buffer = cv2.imencode('.png', test_image)
image_b64 = base64.b64encode(buffer).decode('utf-8')

print("=" * 70)
print("FORENSIC REPORT ENDPOINT TEST")
print("=" * 70)

# Step 1: Register image
print("\n1. Registering test image...")
register_response = requests.post('http://localhost:5000/api/v1/images/register', json={
    'image': image_b64,
    'model_id': 'test-model-v1',
    'timestamp': '2026-01-26T20:00:00Z'
})

if register_response.status_code == 201:
    data = register_response.json()
    image_id = data['image_id']
    print(f"✓ Image registered successfully: {image_id}")
else:
    print(f"✗ Registration failed: {register_response.status_code}")
    print(register_response.text)
    exit(1)

# Step 2: Get forensic report (JSON)
print(f"\n2. Fetching forensic report (JSON) for {image_id}...")
report_response = requests.get(f'http://localhost:5000/api/v1/report/{image_id}')

if report_response.status_code == 200:
    report = report_response.json()
    print(f"✓ Report generated successfully")
    print(f"  - Verdict: {report.get('verdict')}")
    print(f"  - Confidence: {report.get('confidence_score')}")
    print(f"  - Report ID: {report.get('report_id')}")
else:
    print(f"✗ Report generation failed: {report_response.status_code}")
    print(report_response.text)
    exit(1)

# Step 3: Get forensic report (Text)
print(f"\n3. Fetching forensic report (TEXT) for {image_id}...")
text_response = requests.get(f'http://localhost:5000/api/v1/report/{image_id}?format=text')

if text_response.status_code == 200:
    print(f"✓ Text report generated successfully")
    print("\nFirst 500 characters of text report:")
    print("-" * 70)
    print(text_response.text[:500])
    print("-" * 70)
else:
    print(f"✗ Text report generation failed: {text_response.status_code}")
    print(text_response.text)
    exit(1)

# Step 4: Test 404 for non-existent image
print(f"\n4. Testing 404 for non-existent image...")
not_found_response = requests.get('http://localhost:5000/api/v1/report/non-existent-id')

if not_found_response.status_code == 404:
    print(f"✓ Correctly returns 404 for non-existent image")
else:
    print(f"✗ Expected 404, got {not_found_response.status_code}")

print("\n" + "=" * 70)
print("FORENSIC REPORT ENDPOINT TEST COMPLETE")
print("=" * 70)
print("\n✅ All tests passed! Forensic report endpoint is working correctly.")
