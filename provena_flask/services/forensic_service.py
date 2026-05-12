"""
Forensic Report Service for Provena.

Generates comprehensive forensic reports for image provenance verification.
Includes verdict, confidence scoring, and system limitations.
"""

from typing import Dict, Any, Optional
from datetime import datetime
import logging

from provena_flask.services.registry_service import RegistryService
from provena_flask.services.crypto_service import CryptoService
from provena_flask.services.phash_service import PerceptualHashService
from provena_flask.services.watermark_service import WatermarkService

logger = logging.getLogger(__name__)


class ForensicReportService:
    """
    Forensic report generator for provenance verification.
    
    Analyzes:
    - Cryptographic signature validity
    - Watermark presence and integrity
    - Perceptual hash matching
    - Overall confidence score
    - System limitations
    """
    
    # Verdict levels
    VERDICT_AUTHENTIC = "authentic"
    VERDICT_LIKELY_AUTHENTIC = "likely_authentic"
    VERDICT_SUSPICIOUS = "suspicious"
    VERDICT_TAMPERED = "tampered"
    VERDICT_NOT_FOUND = "not_found"
    
    def __init__(self):
        """Initialize forensic report service."""
        self.registry = RegistryService()
        self.phash_service = PerceptualHashService()
        logger.info("Forensic report service initialized")
    
    def generate_report(self, image_id: str, verification_data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Generate comprehensive forensic report for an image.
        
        Args:
            image_id: Image identifier
            verification_data: Optional verification results from verify endpoint
        
        Returns:
            dict: Forensic report with verdict, confidence, and details
        """
        # Get provenance record
        provenance = self.registry.get_provenance(image_id)
        
        if not provenance:
            return self._generate_not_found_report(image_id)
        
        # Analyze provenance record
        analysis = self._analyze_provenance(provenance, verification_data)
        
        # Calculate confidence score
        confidence = self._calculate_confidence(analysis)
        
        # Determine verdict
        verdict = self._determine_verdict(analysis, confidence)
        
        # Generate report
        report = {
            'report_id': f"report-{image_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'image_id': image_id,
            'generated_at': datetime.utcnow().isoformat(),
            'verdict': verdict,
            'confidence_score': confidence,
            'provenance': {
                'model_id': provenance['model_id'],
                'timestamp': provenance['timestamp'],
                'key_id': provenance['key_id'],
                'registered_at': provenance['created_at']
            },
            'verification': analysis,
            'integrity_assessment': self._assess_integrity(analysis, confidence),
            'system_limitations': self._get_system_limitations(),
            'recommendations': self._get_recommendations(verdict, analysis)
        }
        
        logger.info(f"Generated forensic report for {image_id}: verdict={verdict}, confidence={confidence:.2f}")
        
        return report
    
    def _analyze_provenance(self, provenance: Dict, verification_data: Optional[Dict]) -> Dict[str, Any]:
        """
        Analyze provenance record and verification data.
        
        Returns:
            dict: Analysis results
        """
        analysis = {
            'signature_valid': False,
            'watermark_present': False,
            'perceptual_match': False,
            'signature_details': {},
            'watermark_details': {},
            'perceptual_details': {}
        }
        
        # Verify signature
        try:
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
            
            analysis['signature_valid'] = signature_valid
            analysis['signature_details'] = {
                'algorithm': 'Ed25519',
                'key_id': provenance['key_id'],
                'signature_length': len(provenance['signature']),
                'valid': signature_valid
            }
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            analysis['signature_details'] = {'error': str(e)}
        
        # Check watermark (if verification data provided)
        if verification_data:
            analysis['watermark_present'] = verification_data.get('watermark_extracted', False)
            analysis['watermark_details'] = {
                'extracted': verification_data.get('watermark_extracted', False),
                'method': 'DWT (Discrete Wavelet Transform)'
            }
            
            analysis['perceptual_match'] = verification_data.get('perceptual_match', False)
            analysis['perceptual_details'] = {
                'matched': verification_data.get('perceptual_match', False),
                'algorithm': 'pHash (DCT-based)'
            }
        else:
            # No verification data provided
            analysis['watermark_details'] = {
                'status': 'not_verified',
                'note': 'Image not provided for watermark extraction'
            }
            analysis['perceptual_details'] = {
                'status': 'not_verified',
                'note': 'Image not provided for perceptual hash comparison'
            }
        
        return analysis
    
    def _calculate_confidence(self, analysis: Dict) -> float:
        """
        Calculate confidence score (0.0 - 1.0).
        
        Scoring:
        - Signature valid: +0.5
        - Watermark present: +0.3
        - Perceptual match: +0.2
        """
        confidence = 0.0
        
        if analysis['signature_valid']:
            confidence += 0.5
        
        if analysis['watermark_present']:
            confidence += 0.3
        
        if analysis['perceptual_match']:
            confidence += 0.2
        
        return round(confidence, 2)
    
    def _determine_verdict(self, analysis: Dict, confidence: float) -> str:
        """
        Determine overall verdict based on analysis and confidence.
        
        Verdicts:
        - authentic: All checks pass (confidence = 1.0)
        - likely_authentic: Signature valid, some checks pass (confidence >= 0.5)
        - suspicious: Mixed results (confidence < 0.5)
        - tampered: Signature invalid
        """
        if confidence >= 1.0:
            return self.VERDICT_AUTHENTIC
        elif confidence >= 0.7 and analysis['signature_valid']:
            return self.VERDICT_LIKELY_AUTHENTIC
        elif confidence >= 0.5 and analysis['signature_valid']:
            return self.VERDICT_LIKELY_AUTHENTIC
        elif not analysis['signature_valid']:
            return self.VERDICT_TAMPERED
        else:
            return self.VERDICT_SUSPICIOUS
    
    def _assess_integrity(self, analysis: Dict, confidence: float) -> Dict[str, Any]:
        """
        Provide detailed integrity assessment.
        """
        assessment = {
            'overall_status': 'pass' if confidence >= 0.7 else 'fail' if confidence < 0.5 else 'warning',
            'checks': []
        }
        
        # Signature check
        assessment['checks'].append({
            'name': 'Cryptographic Signature',
            'status': 'pass' if analysis['signature_valid'] else 'fail',
            'importance': 'critical',
            'details': 'Ed25519 digital signature verification'
        })
        
        # Watermark check
        if 'status' not in analysis['watermark_details']:
            watermark_status = 'pass' if analysis['watermark_present'] else 'fail'
            assessment['checks'].append({
                'name': 'Invisible Watermark',
                'status': watermark_status,
                'importance': 'high',
                'details': 'DWT-based frequency-domain watermark'
            })
        
        # Perceptual hash check
        if 'status' not in analysis['perceptual_details']:
            phash_status = 'pass' if analysis['perceptual_match'] else 'fail'
            assessment['checks'].append({
                'name': 'Perceptual Hash Match',
                'status': phash_status,
                'importance': 'medium',
                'details': 'pHash similarity comparison'
            })
        
        return assessment
    
    def _get_system_limitations(self) -> Dict[str, Any]:
        """
        Document system limitations for transparency.
        """
        return {
            'watermark_robustness': {
                'limitation': 'Watermark may be lost with heavy compression or significant modifications',
                'tested_against': ['JPEG Q≥75', '±10% resizing'],
                'not_robust_to': ['Extreme compression (Q<50)', 'Cropping', 'Rotation', 'Heavy filtering']
            },
            'perceptual_hash': {
                'limitation': 'pHash is for similarity detection, not cryptographic proof',
                'purpose': 'Tolerant matching for modified images',
                'hamming_threshold': 30
            },
            'signature': {
                'limitation': 'Signature only proves metadata integrity, not image content',
                'what_it_proves': 'Metadata was signed by holder of private key',
                'what_it_does_not_prove': 'Image pixels are unmodified'
            },
            'timestamp': {
                'limitation': 'Timestamp is self-reported, not independently verified',
                'trust_model': 'Relies on AI model provider honesty'
            },
            'key_security': {
                'limitation': 'Security depends on private key protection',
                'risk': 'Compromised private key allows forged signatures'
            }
        }
    
    def _get_recommendations(self, verdict: str, analysis: Dict) -> list:
        """
        Provide recommendations based on verdict.
        """
        recommendations = []
        
        if verdict == self.VERDICT_AUTHENTIC:
            recommendations.append("Image provenance verified with high confidence")
            recommendations.append("All integrity checks passed")
        
        elif verdict == self.VERDICT_LIKELY_AUTHENTIC:
            recommendations.append("Image likely authentic but some checks failed")
            if not analysis['watermark_present']:
                recommendations.append("Watermark not detected - image may have been compressed or modified")
            if not analysis['perceptual_match']:
                recommendations.append("Perceptual hash mismatch - image content may differ from original")
        
        elif verdict == self.VERDICT_SUSPICIOUS:
            recommendations.append("Exercise caution - integrity checks show mixed results")
            recommendations.append("Manual review recommended")
        
        elif verdict == self.VERDICT_TAMPERED:
            recommendations.append("CRITICAL: Cryptographic signature invalid")
            recommendations.append("Image metadata has been tampered with or signature is forged")
            recommendations.append("Do not trust this image's provenance claim")
        
        return recommendations
    
    def _generate_not_found_report(self, image_id: str) -> Dict[str, Any]:
        """Generate report for non-existent image."""
        return {
            'report_id': f"report-{image_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'image_id': image_id,
            'generated_at': datetime.utcnow().isoformat(),
            'verdict': self.VERDICT_NOT_FOUND,
            'confidence_score': 0.0,
            'error': 'No provenance record found',
            'recommendations': [
                'Image not registered in provenance system',
                'Cannot verify authenticity without provenance record'
            ]
        }
    
    def generate_human_readable_report(self, report: Dict) -> str:
        """
        Generate human-readable text report with emphasis on cryptographic provenance.
        
        Args:
            report: JSON report from generate_report()
        
        Returns:
            str: Formatted text report
        """
        lines = []
        lines.append("="*70)
        lines.append("PROVENA: AI IMAGE PROVENANCE & FORENSIC VERIFICATION REPORT")
        lines.append("="*70)
        lines.append(f"Report ID: {report['report_id']}")
        lines.append(f"Image ID: {report['image_id']}")
        lines.append(f"Generated: {report['generated_at']}")
        lines.append("")
        
        # Verdict
        lines.append("VERDICT")
        lines.append("-"*70)
        verdict_display = report['verdict'].upper().replace('_', ' ')
        lines.append(f"Status: {verdict_display}")
        lines.append(f"Confidence Score: {report['confidence_score']:.0%}")
        lines.append("")
        
        # Evidence Summary (NEW)
        if 'verification' in report:
            lines.append("EVIDENCE SUMMARY")
            lines.append("-"*70)
            lines.append("")
            
            # Tier 1: Cryptographic Proof (PRIMARY)
            lines.append("Tier 1 - Cryptographic Proof (AUTHORITATIVE):")
            sig_status = "✓ VALID" if report['verification']['signature_valid'] else "✗ INVALID"
            lines.append(f"  • Digital Signature (Ed25519): {sig_status}")
            if report['verification']['signature_valid']:
                lines.append(f"    → Metadata integrity cryptographically verified")
                lines.append(f"    → Signed by key: {report.get('provenance', {}).get('key_id', 'N/A')}")
            else:
                lines.append(f"    → CRITICAL: Signature verification failed")
                lines.append(f"    → Metadata may be tampered or forged")
            lines.append("")
            
            # Tier 2: Perceptual Matching (SECONDARY)
            if 'perceptual_match' in report['verification']:
                lines.append("Tier 2 - Perceptual Verification (ROBUST):")
                phash_status = "✓ MATCH" if report['verification']['perceptual_match'] else "✗ NO MATCH"
                lines.append(f"  • Perceptual Hash: {phash_status}")
                if report['verification']['perceptual_match']:
                    lines.append(f"    → Image perceptually similar to registered image")
                    lines.append(f"    → Tolerant to compression and minor modifications")
                else:
                    lines.append(f"    → Image differs significantly from registered version")
                lines.append("")
            
            # Tier 3: Watermark (SUPPLEMENTARY)
            if 'watermark_present' in report['verification']:
                lines.append("Tier 3 - Forensic Watermark (SUPPLEMENTARY):")
                wm_status = "✓ EXTRACTED" if report['verification']['watermark_present'] else "✗ NOT EXTRACTED"
                lines.append(f"  • Invisible Watermark: {wm_status}")
                if report['verification']['watermark_present']:
                    lines.append(f"    → Forensic trace successfully extracted")
                    lines.append(f"    → Provides supplementary evidence")
                else:
                    lines.append(f"    → Watermark not detected (may be lost to compression)")
                    lines.append(f"    → This does NOT invalidate cryptographic proof")
                lines.append("")
        
        # Provenance (if exists)
        if 'provenance' in report:
            lines.append("PROVENANCE INFORMATION")
            lines.append("-"*70)
            lines.append(f"Model: {report['provenance']['model_id']}")
            lines.append(f"Timestamp: {report['provenance']['timestamp']}")
            lines.append(f"Key ID: {report['provenance']['key_id']}")
            lines.append(f"Registered: {report['provenance']['registered_at']}")
            lines.append("")
        
        # Integrity Assessment
        if 'integrity_assessment' in report:
            lines.append("INTEGRITY ASSESSMENT")
            lines.append("-"*70)
            for check in report['integrity_assessment']['checks']:
                status_symbol = "✓" if check['status'] == 'pass' else "✗"
                lines.append(f"{status_symbol} {check['name']}: {check['status'].upper()}")
                lines.append(f"  Importance: {check['importance']}")
                lines.append(f"  Details: {check['details']}")
            lines.append("")
        
        # Conclusion (NEW)
        lines.append("CONCLUSION")
        lines.append("-"*70)
        if report['verdict'] == 'authentic':
            lines.append("This image has been verified with high confidence.")
            lines.append("All verification layers (cryptographic, perceptual, watermark) succeeded.")
            lines.append("The image's provenance is cryptographically established.")
        elif report['verdict'] == 'likely_authentic':
            lines.append("This image's provenance is cryptographically verified.")
            lines.append("Some forensic evidence (watermark/perceptual hash) may be missing,")
            lines.append("but the cryptographic signature remains valid and authoritative.")
        elif report['verdict'] == 'tampered':
            lines.append("CRITICAL: Cryptographic signature verification FAILED.")
            lines.append("The metadata has been tampered with or the signature is forged.")
            lines.append("Do NOT trust this image's provenance claim.")
        elif report['verdict'] == 'not_found':
            lines.append("No provenance record found for this image.")
            lines.append("The image was never registered in the system.")
        else:
            lines.append("Verification results are inconclusive.")
            lines.append("Manual review recommended.")
        lines.append("")
        
        # Recommendations
        if 'recommendations' in report:
            lines.append("RECOMMENDATIONS")
            lines.append("-"*70)
            for i, rec in enumerate(report['recommendations'], 1):
                lines.append(f"{i}. {rec}")
            lines.append("")
        
        # System Limitations (ENHANCED)
        if 'system_limitations' in report:
            lines.append("KNOWN SYSTEM LIMITATIONS")
            lines.append("-"*70)
            lines.append("This system has documented limitations:")
            lines.append("")
            lines.append("• Watermark Robustness:")
            lines.append("  Watermarks may fail under JPEG compression, resizing, or format")
            lines.append("  conversion. This is EXPECTED and does NOT invalidate cryptographic")
            lines.append("  provenance if the signature is valid.")
            lines.append("")
            lines.append("• Cryptographic Signatures:")
            lines.append("  Signatures prove metadata integrity, NOT pixel-level integrity.")
            lines.append("  An image with valid signature may have been edited after generation.")
            lines.append("")
            lines.append("• Timestamp Trust:")
            lines.append("  Timestamps are self-reported by AI providers, not independently")
            lines.append("  verified. Trust relies on provider reputation.")
            lines.append("")
            lines.append("• Provenance ≠ Authenticity:")
            lines.append("  This system proves an image was REGISTERED with specific metadata,")
            lines.append("  not that the image content is genuine or unmodified.")
            lines.append("")
        
        lines.append("="*70)
        lines.append("CRYPTOGRAPHIC PROVENANCE: Authoritative proof via digital signatures")
        lines.append("FORENSIC WATERMARKING: Supplementary evidence when extractable")
        lines.append("="*70)
        
        return "\n".join(lines)


def generate_forensic_report(image_id: str, verification_data: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Generate forensic report for an image.
    
    Args:
        image_id: Image identifier
        verification_data: Optional verification results
    
    Returns:
        dict: Forensic report
    """
    service = ForensicReportService()
    return service.generate_report(image_id, verification_data)
