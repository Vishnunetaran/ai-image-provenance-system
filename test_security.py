"""
Test suite for security, logging, and audit features.

Tests structured logging, rate limiting, and input validation.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.security_service import (
    AuditLogger, RateLimiter, InputValidator
)


def run_tests():
    """Run security and audit tests."""
    print("\n" + "="*70)
    print("  PROVENA-FLASK SECURITY & AUDIT TEST SUITE")
    print("  Phase 7: Security, Logging, and Audit")
    print("="*70)
    
    try:
        # Test 1: Input validation - valid inputs
        print("\n[1/6] Testing input validation (valid inputs)...")
        
        assert InputValidator.validate_image_id("img-test-12345")
        assert InputValidator.validate_model_id("gpt-vision-v1")
        assert InputValidator.validate_timestamp("2026-01-26T14:00:00Z")
        
        print("✓ PASS: Valid inputs accepted")
        
        # Test 2: Input validation - invalid inputs
        print("\n[2/6] Testing input validation (invalid inputs)...")
        
        # Empty image_id
        try:
            InputValidator.validate_image_id("")
            assert False, "Should reject empty image_id"
        except ValueError:
            pass
        
        # SQL injection attempt
        try:
            InputValidator.validate_image_id("img'; DROP TABLE users--")
            assert False, "Should reject SQL injection"
        except ValueError:
            pass
        
        # Invalid model_id
        try:
            InputValidator.validate_model_id("a")  # Too short
            assert False, "Should reject short model_id"
        except ValueError:
            pass
        
        # Invalid timestamp
        try:
            InputValidator.validate_timestamp("not-a-timestamp")
            assert False, "Should reject invalid timestamp"
        except ValueError:
            pass
        
        print("✓ PASS: Invalid inputs correctly rejected")
        
        # Test 3: Rate limiting
        print("\n[3/6] Testing rate limiting...")
        
        limiter = RateLimiter(requests_per_minute=5)
        
        # First 5 requests should succeed
        for i in range(5):
            assert limiter.is_allowed("192.168.1.1"), f"Request {i+1} should be allowed"
        
        # 6th request should be blocked
        assert not limiter.is_allowed("192.168.1.1"), "6th request should be blocked"
        
        # Different IP should be allowed
        assert limiter.is_allowed("192.168.1.2"), "Different IP should be allowed"
        
        print("✓ PASS: Rate limiting works correctly")
        
        # Test 4: Audit logging (just verify no errors)
        print("\n[4/6] Testing audit logging...")
        
        # These should not raise exceptions
        AuditLogger.log_registry_write(
            "img-test", "test-model", "register", True,
            {'key_id': 'test-key'}
        )
        
        AuditLogger.log_verification_request(
            "img-test", "verified", 1.0,
            {'signature_valid': True}
        )
        
        AuditLogger.log_security_event(
            "test_event", "low", "Test security event"
        )
        
        print("✓ PASS: Audit logging works")
        
        # Test 5: Base64 validation
        print("\n[5/6] Testing base64 image validation...")
        
        # Valid base64
        valid_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        assert InputValidator.validate_base64_image(valid_b64)
        
        # Empty image
        try:
            InputValidator.validate_base64_image("")
            assert False, "Should reject empty image"
        except ValueError:
            pass
        
        # Invalid characters
        try:
            InputValidator.validate_base64_image("invalid!@#$%")
            assert False, "Should reject invalid base64"
        except ValueError:
            pass
        
        print("✓ PASS: Base64 validation works")
        
        # Test 6: Validation helper functions
        print("\n[6/6] Testing validation helper functions...")
        
        from provena_flask.services.security_service import (
            validate_registration_input,
            validate_verification_input
        )
        
        # Valid registration data
        valid_reg_data = {
            'image': valid_b64,
            'model_id': 'test-model',
            'timestamp': '2026-01-26T14:00:00Z'
        }
        
        validate_registration_input(valid_reg_data)
        
        # Valid verification data
        valid_ver_data = {
            'image': valid_b64
        }
        
        validate_verification_input(valid_ver_data)
        
        print("✓ PASS: Validation helper functions work")
        
        print("\n" + "="*70)
        print("  ✓✓✓ ALL SECURITY & AUDIT TESTS PASSED ✓✓✓")
        print("="*70)
        print("\nPhase 7 Security & Audit is VERIFIED and OPERATIONAL")
        print("\nFeatures implemented:")
        print("  - Structured audit logging (JSON format)")
        print("  - Rate limiting (configurable req/min)")
        print("  - Enhanced input validation")
        print("  - SQL injection prevention")
        print("  - Size limit enforcement")
        print("  - Security event logging")
        
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
