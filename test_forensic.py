"""
Test suite for forensic reporting.

Tests report generation, verdict determination, and confidence scoring.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

os.environ['FLASK_ENV'] = 'testing'

from provena_flask.services.forensic_service import ForensicReportService
from provena_flask.services.registry_service import RegistryService
from provena_flask.services.crypto_service import CryptoService


def run_tests():
    """Run forensic reporting tests."""
    print("\n" + "="*70)
    print("  PROVENA FORENSIC REPORTING TEST SUITE")
    print("  Phase 6: Forensic Report Generation")
    print("="*70)
    
    try:
        # Setup: Create a test provenance record
        print("\n[Setup] Creating test provenance record...")
        registry = RegistryService('./test_forensic.db')
        
        private_key, public_key = CryptoService.generate_keys()
        key_id = CryptoService.get_key_id(public_key)
        
        test_metadata = {
            'image_id': 'img-forensic-test',
            'model_id': 'test-model',
            'timestamp': '2026-01-26T14:00:00Z',
            'perceptual_hash': 'abc123def456'
        }
        
        signature = CryptoService.sign(test_metadata, private_key)
        
        registry.register_provenance(
            image_id='img-forensic-test',
            model_id='test-model',
            timestamp='2026-01-26T14:00:00Z',
            watermark_payload=b'test_watermark_1',
            perceptual_hash='abc123def456',
            signature=signature,
            public_key=public_key,
            key_id=key_id
        )
        
        print("✓ Test provenance record created")
        
        # Test 1: Generate report for existing image
        print("\n[1/5] Testing report generation for existing image...")
        forensic_service = ForensicReportService()
        forensic_service.registry = registry  # Use test registry
        
        report = forensic_service.generate_report('img-forensic-test')
        
        assert 'report_id' in report
        assert report['image_id'] == 'img-forensic-test'
        assert 'verdict' in report
        assert 'confidence_score' in report
        assert 'provenance' in report
        assert 'integrity_assessment' in report
        assert 'system_limitations' in report
        assert 'recommendations' in report
        
        print(f"   Report ID: {report['report_id']}")
        print(f"   Verdict: {report['verdict']}")
        print(f"   Confidence: {report['confidence_score']:.0%}")
        print("✓ PASS: Report generated successfully")
        
        # Test 2: Verify verdict determination
        print("\n[2/5] Testing verdict determination...")
        
        # With full verification data (all checks pass)
        verification_data = {
            'watermark_extracted': True,
            'perceptual_match': True
        }
        
        report_full = forensic_service.generate_report('img-forensic-test', verification_data)
        assert report_full['verdict'] in ['authentic', 'likely_authentic']
        assert report_full['confidence_score'] >= 0.7
        
        print(f"   Full verification: {report_full['verdict']} ({report_full['confidence_score']:.0%})")
        
        # With partial verification (watermark missing)
        verification_partial = {
            'watermark_extracted': False,
            'perceptual_match': True
        }
        
        report_partial = forensic_service.generate_report('img-forensic-test', verification_partial)
        print(f"   Partial verification: {report_partial['verdict']} ({report_partial['confidence_score']:.0%})")
        
        print("✓ PASS: Verdict determination works")
        
        # Test 3: Confidence scoring
        print("\n[3/5] Testing confidence scoring...")
        
        # Signature only: 0.5
        report_sig_only = forensic_service.generate_report('img-forensic-test')
        assert report_sig_only['confidence_score'] == 0.5
        
        # Signature + watermark: 0.8
        verification_sig_wm = {
            'watermark_extracted': True,
            'perceptual_match': False
        }
        report_sig_wm = forensic_service.generate_report('img-forensic-test', verification_sig_wm)
        assert report_sig_wm['confidence_score'] == 0.8
        
        # All checks: 1.0
        verification_all = {
            'watermark_extracted': True,
            'perceptual_match': True
        }
        report_all = forensic_service.generate_report('img-forensic-test', verification_all)
        assert report_all['confidence_score'] == 1.0
        
        print(f"   Signature only: {report_sig_only['confidence_score']:.1f}")
        print(f"   Signature + watermark: {report_sig_wm['confidence_score']:.1f}")
        print(f"   All checks: {report_all['confidence_score']:.1f}")
        print("✓ PASS: Confidence scoring accurate")
        
        # Test 4: Human-readable report
        print("\n[4/5] Testing human-readable report generation...")
        
        text_report = forensic_service.generate_human_readable_report(report_full)
        
        assert 'FORENSIC REPORT' in text_report
        assert 'VERDICT' in text_report
        assert 'INTEGRITY ASSESSMENT' in text_report
        assert 'RECOMMENDATIONS' in text_report
        assert 'SYSTEM LIMITATIONS' in text_report
        
        print(f"   Report length: {len(text_report)} characters")
        print(f"   Contains all sections: ✓")
        print("✓ PASS: Human-readable report generated")
        
        # Test 5: Not found report
        print("\n[5/5] Testing report for non-existent image...")
        
        report_not_found = forensic_service.generate_report('img-nonexistent')
        
        assert report_not_found['verdict'] == 'not_found'
        assert report_not_found['confidence_score'] == 0.0
        assert 'error' in report_not_found
        
        print(f"   Verdict: {report_not_found['verdict']}")
        print(f"   Error message: {report_not_found['error']}")
        print("✓ PASS: Not found report generated")
        
        print("\n" + "="*70)
        print("  ✓✓✓ ALL FORENSIC REPORTING TESTS PASSED ✓✓✓")
        print("="*70)
        print("\nPhase 6 Forensic Reporting is VERIFIED and OPERATIONAL")
        print("\nReport Features:")
        print("  - Verdict determination (5 levels)")
        print("  - Confidence scoring (0.0-1.0)")
        print("  - Integrity assessment")
        print("  - System limitations documentation")
        print("  - Recommendations")
        print("  - Human-readable format")
        
        # Cleanup
        try:
            if os.path.exists('./test_forensic.db'):
                os.remove('./test_forensic.db')
        except:
            pass  # Ignore cleanup errors on Windows
        
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
