"""
Simplified registry test suite that works reliably on Windows.
"""

import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.models.provenance import ProvenanceDatabase
from provena_flask.services.registry_service import RegistryService
from provena_flask.services.crypto_service import CryptoService


def run_tests():
    """Run all registry tests in a single database instance."""
    print("\n" + "="*70)
    print("  PROVENA REGISTRY TEST SUITE")
    print("  Phase 2: Append-Only Provenance Registry")
    print("="*70)
    
    # Use a single test database
    test_db_path = "./test_registry_temp.db"
    
    try:
        # Clean up any existing test database
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
        
        print("\n[1/9] Testing database initialization...")
        db = ProvenanceDatabase(test_db_path)
        assert os.path.exists(test_db_path)
        print("✓ PASS: Database initialized")
        
        print("\n[2/9] Testing record insertion...")
        private_key, public_key = CryptoService.generate_keys()
        key_id = CryptoService.get_key_id(public_key)
        
        record1 = {
            'image_id': 'img-test-001',
            'model_id': 'test-model-v1',
            'timestamp': datetime.utcnow().isoformat(),
            'watermark_payload': b'watermark_data_1234567890',
            'perceptual_hash': 'phash:test001',
            'signature': b'0' * 64,
            'public_key': public_key,
            'key_id': key_id
        }
        
        success = db.insert_record(record1)
        assert success, "First insert should succeed"
        print("✓ PASS: Record inserted")
        
        print("\n[3/9] Testing append-only constraint...")
        success_dup = db.insert_record(record1)
        assert not success_dup, "Duplicate should be rejected"
        print("✓ PASS: Duplicate correctly rejected")
        
        print("\n[4/9] Testing record retrieval...")
        retrieved = db.get_record_by_image_id('img-test-001')
        assert retrieved is not None
        assert retrieved['model_id'] == 'test-model-v1'
        print("✓ PASS: Record retrieved successfully")
        
        print("\n[5/9] Testing record count...")
        count = db.count_records()
        assert count == 1, f"Expected 1 record, got {count}"
        print(f"✓ PASS: Record count correct ({count})")
        
        print("\n[6/9] Testing multiple inserts...")
        for i in range(2, 6):
            record = {
                'image_id': f'img-test-{i:03d}',
                'model_id': f'model-{i % 2}',
                'timestamp': datetime.utcnow().isoformat(),
                'watermark_payload': f'watermark_{i}_1234567890'.encode(),
                'perceptual_hash': f'phash:test{i:03d}',
                'signature': b'0' * 64,
                'public_key': public_key,
                'key_id': key_id
            }
            db.insert_record(record)
        
        total = db.count_records()
        assert total == 5, f"Expected 5 records, got {total}"
        print(f"✓ PASS: Multiple inserts successful ({total} records)")
        
        print("\n[7/9] Testing pagination...")
        page1 = db.list_records(limit=3, offset=0)
        page2 = db.list_records(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2
        print(f"✓ PASS: Pagination works (page1={len(page1)}, page2={len(page2)})")
        
        print("\n[8/9] Testing model search...")
        results = db.search_by_model_id('model-0')
        assert len(results) >= 1
        print(f"✓ PASS: Model search works ({len(results)} results)")
        
        print("\n[9/9] Testing registry service...")
        service = RegistryService(test_db_path)
        
        result = service.register_provenance(
            image_id='img-service-test',
            model_id='service-model',
            timestamp=datetime.utcnow().isoformat(),
            watermark_payload=b'service_watermark_1234567890',
            perceptual_hash='phash:service',
            signature=b'0' * 64,
            public_key=public_key,
            key_id=key_id
        )
        
        assert result['status'] == 'success'
        
        stats = service.get_statistics()
        assert stats['total_records'] == 6
        assert stats['integrity_valid']
        
        print(f"✓ PASS: Registry service works ({stats['total_records']} records)")
        
        print("\n" + "="*70)
        print("  ✓✓✓ ALL 9 TESTS PASSED ✓✓✓")
        print("="*70)
        print("\nPhase 2 Append-Only Registry is VERIFIED and OPERATIONAL")
        
        return True
        
    except AssertionError as e:
        print(f"\n✗ FAIL: {str(e)}")
        return False
    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Clean up test database
        try:
            if os.path.exists(test_db_path):
                os.remove(test_db_path)
        except:
            pass


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
