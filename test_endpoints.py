"""
Simple endpoint verification using urllib (built-in).
"""
import urllib.request
import json

BASE_URL = "http://localhost:5000"

endpoints = [
    "/health",
    "/api/v1/status",
    "/registry/status",
    "/watermark/status",
    "/verification/status",
    "/reports/status"
]

print("=" * 70)
print("  Provena Phase 0 Verification")
print("=" * 70)

all_passed = True

for endpoint in endpoints:
    try:
        with urllib.request.urlopen(f"{BASE_URL}{endpoint}", timeout=2) as response:
            data = json.loads(response.read().decode())
            print(f"✓ {endpoint:<30} | {data}")
    except Exception as e:
        print(f"✗ {endpoint:<30} | Error: {str(e)}")
        all_passed = False

print("=" * 70)
if all_passed:
    print("✓✓✓ Phase 0 Complete: All endpoints operational!")
else:
    print("✗ Some endpoints failed")
print("=" * 70)
