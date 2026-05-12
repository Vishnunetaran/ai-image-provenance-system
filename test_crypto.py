"""
Comprehensive test suite for cryptographic services.

Tests Ed25519 signature operations and key storage security.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.crypto_service import CryptoService, generate_keys, sign, verify, get_key_id
from provena_flask.services.key_storage import KeyStorageService


def test_key_generation():
    """Test Ed25519 key pair generation."""
    print("\n" + "="*70)
    print("TEST 1: Key Generation")
    print("="*70)
    
    private_key, public_key = generate_keys()
    
    # Verify keys are bytes
    assert isinstance(private_key, bytes), "Private key should be bytes"
    assert isinstance(public_key, bytes), "Public key should be bytes"
    
    # Verify keys are PEM-encoded (start with correct headers)
    assert private_key.startswith(b'-----BEGIN PRIVATE KEY-----'), "Private key should be PEM-encoded"
    assert public_key.startswith(b'-----BEGIN PUBLIC KEY-----'), "Public key should be PEM-encoded"
    
    # Verify reasonable key sizes
    assert len(private_key) > 100, "Private key should be substantial"
    assert len(public_key) > 50, "Public key should be substantial"
    
    print(f"✓ Generated private key: {len(private_key)} bytes")
    print(f"✓ Generated public key: {len(public_key)} bytes")
    print(f"✓ Keys are properly PEM-encoded")
    print("✓ PASS: Key generation successful")


def test_signing_and_verification():
    """Test signing and verification of metadata."""
    print("\n" + "="*70)
    print("TEST 2: Signing and Verification")
    print("="*70)
    
    # Generate keys
    private_key, public_key = generate_keys()
    
    # Create test metadata
    metadata = {
        "image_id": "test-image-123",
        "model_id": "gpt-vision-v1",
        "timestamp": "2026-01-26T13:00:00Z",
        "prompt_hash": "abc123def456"
    }
    
    # Sign metadata
    signature = sign(metadata, private_key)
    
    # Verify signature format
    assert isinstance(signature, bytes), "Signature should be bytes"
    assert len(signature) == 64, f"Ed25519 signature should be 64 bytes, got {len(signature)}"
    
    print(f"✓ Signed metadata: {metadata}")
    print(f"✓ Signature: {signature.hex()[:32]}... ({len(signature)} bytes)")
    
    # Verify signature
    is_valid = verify(metadata, signature, public_key)
    assert is_valid, "Signature verification should succeed"
    
    print(f"✓ Signature verified successfully")
    print("✓ PASS: Signing and verification successful")


def test_signature_tampering_detection():
    """Test that tampered data fails verification."""
    print("\n" + "="*70)
    print("TEST 3: Tampering Detection")
    print("="*70)
    
    # Generate keys
    private_key, public_key = generate_keys()
    
    # Original metadata
    original_metadata = {
        "image_id": "original-123",
        "model_id": "model-v1"
    }
    
    # Sign original
    signature = sign(original_metadata, private_key)
    
    # Tamper with metadata
    tampered_metadata = {
        "image_id": "tampered-456",  # Changed!
        "model_id": "model-v1"
    }
    
    # Verify tampered data
    is_valid = verify(tampered_metadata, signature, public_key)
    assert not is_valid, "Tampered data should fail verification"
    
    print(f"✓ Original metadata: {original_metadata}")
    print(f"✓ Tampered metadata: {tampered_metadata}")
    print(f"✓ Signature correctly rejected tampered data")
    print("✓ PASS: Tampering detection successful")


def test_signature_with_different_key():
    """Test that signature fails with wrong public key."""
    print("\n" + "="*70)
    print("TEST 4: Wrong Key Detection")
    print("="*70)
    
    # Generate two key pairs
    private_key1, public_key1 = generate_keys()
    private_key2, public_key2 = generate_keys()
    
    metadata = {"image_id": "test-123"}
    
    # Sign with key 1
    signature = sign(metadata, private_key1)
    
    # Try to verify with key 2 (wrong key)
    is_valid = verify(metadata, signature, public_key2)
    assert not is_valid, "Signature should fail with wrong public key"
    
    # Verify with correct key
    is_valid_correct = verify(metadata, signature, public_key1)
    assert is_valid_correct, "Signature should succeed with correct public key"
    
    print(f"✓ Signature rejected with wrong public key")
    print(f"✓ Signature accepted with correct public key")
    print("✓ PASS: Key mismatch detection successful")


def test_key_id_generation():
    """Test key ID generation for key identification."""
    print("\n" + "="*70)
    print("TEST 5: Key ID Generation")
    print("="*70)
    
    private_key, public_key = generate_keys()
    
    # Generate key ID
    key_id = get_key_id(public_key)
    
    # Verify key ID format
    assert isinstance(key_id, str), "Key ID should be string"
    assert len(key_id) == 32, f"Key ID should be 32 hex characters, got {len(key_id)}"
    assert all(c in '0123456789abcdef' for c in key_id), "Key ID should be hex"
    
    # Verify deterministic (same key = same ID)
    key_id2 = get_key_id(public_key)
    assert key_id == key_id2, "Key ID should be deterministic"
    
    # Verify different keys have different IDs
    _, public_key2 = generate_keys()
    key_id3 = get_key_id(public_key2)
    assert key_id != key_id3, "Different keys should have different IDs"
    
    print(f"✓ Key ID: {key_id}")
    print(f"✓ Key ID is deterministic")
    print(f"✓ Different keys have different IDs")
    print("✓ PASS: Key ID generation successful")


def test_key_storage():
    """Test secure key storage operations."""
    print("\n" + "="*70)
    print("TEST 6: Key Storage")
    print("="*70)
    
    # Create temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Initialize key storage
        storage = KeyStorageService(temp_dir)
        
        # Generate and store keys
        key_id = storage.generate_and_store_keys("test_key")
        
        print(f"✓ Generated and stored key pair with ID: {key_id}")
        
        # Verify keys exist
        assert storage.key_exists("test_key"), "Keys should exist after generation"
        
        # Load keys
        private_key = storage.load_private_key("test_key")
        public_key = storage.load_public_key("test_key")
        
        assert private_key.startswith(b'-----BEGIN PRIVATE KEY-----'), "Loaded private key should be valid"
        assert public_key.startswith(b'-----BEGIN PUBLIC KEY-----'), "Loaded public key should be valid"
        
        print(f"✓ Successfully loaded private key ({len(private_key)} bytes)")
        print(f"✓ Successfully loaded public key ({len(public_key)} bytes)")
        
        # Verify key ID matches
        stored_key_id = storage.get_key_id("test_key")
        assert stored_key_id == key_id, "Stored key ID should match generated ID"
        
        print(f"✓ Key ID matches: {stored_key_id}")
        
        # List keys
        keys = storage.list_keys()
        assert "test_key" in keys, "test_key should be in list"
        
        print(f"✓ Listed keys: {keys}")
        
        # Test signing with stored keys
        metadata = {"test": "data"}
        signature = sign(metadata, private_key)
        is_valid = verify(metadata, signature, public_key)
        
        assert is_valid, "Signature with stored keys should verify"
        
        print(f"✓ Signing and verification with stored keys successful")
        
        # Delete keys
        deleted = storage.delete_keys("test_key")
        assert deleted, "Keys should be deleted"
        assert not storage.key_exists("test_key"), "Keys should not exist after deletion"
        
        print(f"✓ Keys deleted successfully")
        print("✓ PASS: Key storage operations successful")
        
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir)


def test_key_storage_security():
    """Test that duplicate key generation is prevented."""
    print("\n" + "="*70)
    print("TEST 7: Key Storage Security")
    print("="*70)
    
    temp_dir = tempfile.mkdtemp()
    
    try:
        storage = KeyStorageService(temp_dir)
        
        # Generate first key
        storage.generate_and_store_keys("duplicate_test")
        
        print(f"✓ Generated first key pair")
        
        # Try to generate duplicate (should fail)
        try:
            storage.generate_and_store_keys("duplicate_test")
            assert False, "Should not allow duplicate key generation"
        except FileExistsError as e:
            print(f"✓ Correctly prevented duplicate key generation: {str(e)[:50]}...")
        
        # Try to load non-existent key
        try:
            storage.load_private_key("nonexistent")
            assert False, "Should not load non-existent key"
        except FileNotFoundError as e:
            print(f"✓ Correctly raised error for non-existent key: {str(e)[:50]}...")
        
        print("✓ PASS: Key storage security checks successful")
        
    finally:
        shutil.rmtree(temp_dir)


def test_canonical_json_signing():
    """Test that JSON serialization is canonical for signing."""
    print("\n" + "="*70)
    print("TEST 8: Canonical JSON Signing")
    print("="*70)
    
    private_key, public_key = generate_keys()
    
    # Same data, different order
    metadata1 = {"b": 2, "a": 1, "c": 3}
    metadata2 = {"a": 1, "c": 3, "b": 2}
    
    # Sign both
    signature1 = sign(metadata1, private_key)
    signature2 = sign(metadata2, private_key)
    
    # Signatures should be identical (canonical JSON)
    assert signature1 == signature2, "Signatures should be identical for same data in different order"
    
    print(f"✓ Metadata 1: {metadata1}")
    print(f"✓ Metadata 2: {metadata2}")
    print(f"✓ Signatures are identical (canonical JSON)")
    print("✓ PASS: Canonical JSON signing successful")


def run_all_tests():
    """Run all cryptographic tests."""
    print("\n" + "="*70)
    print("  PROVENA CRYPTOGRAPHIC MODULE TEST SUITE")
    print("  Phase 1: Ed25519 Digital Signatures & Key Storage")
    print("="*70)
    
    tests = [
        test_key_generation,
        test_signing_and_verification,
        test_signature_tampering_detection,
        test_signature_with_different_key,
        test_key_id_generation,
        test_key_storage,
        test_key_storage_security,
        test_canonical_json_signing
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\n✗ FAIL: {test.__name__}")
            print(f"  Error: {str(e)}")
            failed += 1
        except Exception as e:
            print(f"\n✗ ERROR: {test.__name__}")
            print(f"  Exception: {str(e)}")
            failed += 1
    
    print("\n" + "="*70)
    print(f"  TEST RESULTS: {passed} passed, {failed} failed")
    print("="*70)
    
    if failed == 0:
        print("\n✓✓✓ ALL TESTS PASSED ✓✓✓")
        print("\nPhase 1 Cryptographic Core is VERIFIED and OPERATIONAL")
        return True
    else:
        print(f"\n✗✗✗ {failed} TESTS FAILED ✗✗✗")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
