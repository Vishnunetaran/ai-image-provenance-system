"""
Quick demonstration of the cryptographic module capabilities.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.crypto_service import CryptoService
import json

print("="*70)
print("  Provena-FLASK Cryptographic Module Demo")
print("="*70)

# 1. Generate keys
print("\n1. Generating Ed25519 key pair...")
private_key, public_key = CryptoService.generate_keys()
print(f"   ✓ Private key: {len(private_key)} bytes")
print(f"   ✓ Public key: {len(public_key)} bytes")

# 2. Create metadata
metadata = {
    "image_id": "img-abc123",
    "model_id": "gpt-vision-v1",
    "timestamp": "2026-01-26T13:00:00Z",
    "prompt_hash": "sha256:abc123..."
}
print(f"\n2. Metadata to sign:")
print(f"   {json.dumps(metadata, indent=3)}")

# 3. Sign metadata
print(f"\n3. Signing metadata...")
signature = CryptoService.sign(metadata, private_key)
print(f"   ✓ Signature: {signature.hex()[:64]}...")
print(f"   ✓ Signature length: {len(signature)} bytes (Ed25519 standard)")

# 4. Verify signature
print(f"\n4. Verifying signature...")
is_valid = CryptoService.verify(metadata, signature, public_key)
print(f"   ✓ Verification result: {is_valid}")

# 5. Test tampering detection
print(f"\n5. Testing tampering detection...")
tampered_metadata = metadata.copy()
tampered_metadata["image_id"] = "TAMPERED"
is_valid_tampered = CryptoService.verify(tampered_metadata, signature, public_key)
print(f"   ✓ Tampered data verification: {is_valid_tampered} (should be False)")

# 6. Get key ID
print(f"\n6. Generating key identifier...")
key_id = CryptoService.get_key_id(public_key)
print(f"   ✓ Key ID: {key_id}")

print("\n" + "="*70)
print("  ✓ Cryptographic module operational!")
print("="*70)
