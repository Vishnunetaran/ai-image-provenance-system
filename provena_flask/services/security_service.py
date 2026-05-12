"""
Security and Audit Service for Provena.

Provides structured logging, audit trails, and security utilities.
"""

import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional
from functools import wraps
from flask import request, g
import time

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Structured audit logging for security-critical operations.
    
    Logs all registry writes, verification requests, and API access.
    """
    
    @staticmethod
    def log_registry_write(image_id: str, model_id: str, operation: str, success: bool, details: Optional[Dict] = None):
        """
        Log registry write operations.
        
        Args:
            image_id: Image identifier
            model_id: Model identifier
            operation: Operation type (register, update, delete)
            success: Whether operation succeeded
            details: Optional additional details
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'registry_write',
            'operation': operation,
            'image_id': image_id,
            'model_id': model_id,
            'success': success,
            'ip_address': request.remote_addr if request else 'unknown',
            'user_agent': request.headers.get('User-Agent', 'unknown') if request else 'unknown'
        }
        
        if details:
            log_entry['details'] = details
        
        if success:
            logger.info(f"AUDIT: Registry write - {json.dumps(log_entry)}")
        else:
            logger.warning(f"AUDIT: Registry write failed - {json.dumps(log_entry)}")
    
    @staticmethod
    def log_verification_request(image_id: Optional[str], verdict: str, confidence: float, details: Optional[Dict] = None):
        """
        Log verification requests.
        
        Args:
            image_id: Image identifier (if found)
            verdict: Verification verdict
            confidence: Confidence score
            details: Optional additional details
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'verification_request',
            'image_id': image_id or 'unknown',
            'verdict': verdict,
            'confidence': confidence,
            'ip_address': request.remote_addr if request else 'unknown',
            'user_agent': request.headers.get('User-Agent', 'unknown') if request else 'unknown'
        }
        
        if details:
            log_entry['details'] = details
        
        logger.info(f"AUDIT: Verification - {json.dumps(log_entry)}")
    
    @staticmethod
    def log_api_access(endpoint: str, method: str, status_code: int, duration_ms: float):
        """
        Log API access.
        
        Args:
            endpoint: API endpoint
            method: HTTP method
            status_code: Response status code
            duration_ms: Request duration in milliseconds
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'api_access',
            'endpoint': endpoint,
            'method': method,
            'status_code': status_code,
            'duration_ms': round(duration_ms, 2),
            'ip_address': request.remote_addr if request else 'unknown'
        }
        
        logger.info(f"AUDIT: API access - {json.dumps(log_entry)}")
    
    @staticmethod
    def log_security_event(event_type: str, severity: str, message: str, details: Optional[Dict] = None):
        """
        Log security events.
        
        Args:
            event_type: Type of security event
            severity: Severity level (low, medium, high, critical)
            message: Event message
            details: Optional additional details
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'security_event',
            'security_event_type': event_type,
            'severity': severity,
            'message': message,
            'ip_address': request.remote_addr if request else 'unknown'
        }
        
        if details:
            log_entry['details'] = details
        
        if severity in ['high', 'critical']:
            logger.error(f"SECURITY: {json.dumps(log_entry)}")
        elif severity == 'medium':
            logger.warning(f"SECURITY: {json.dumps(log_entry)}")
        else:
            logger.info(f"SECURITY: {json.dumps(log_entry)}")


class RateLimiter:
    """
    Simple in-memory rate limiter for API endpoints.
    
    Note: For production, use Redis-based rate limiting.
    """
    
    def __init__(self, requests_per_minute: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_minute: Maximum requests per minute per IP
        """
        self.requests_per_minute = requests_per_minute
        self.requests = {}  # {ip: [(timestamp, count), ...]}
        logger.info(f"Rate limiter initialized: {requests_per_minute} req/min")
    
    def is_allowed(self, ip_address: str) -> bool:
        """
        Check if request is allowed.
        
        Args:
            ip_address: Client IP address
        
        Returns:
            bool: True if allowed, False if rate limited
        """
        now = time.time()
        minute_ago = now - 60
        
        # Clean old entries
        if ip_address in self.requests:
            self.requests[ip_address] = [
                (ts, count) for ts, count in self.requests[ip_address]
                if ts > minute_ago
            ]
        else:
            self.requests[ip_address] = []
        
        # Count requests in last minute
        total_requests = sum(count for _, count in self.requests[ip_address])
        
        if total_requests >= self.requests_per_minute:
            AuditLogger.log_security_event(
                'rate_limit_exceeded',
                'medium',
                f'Rate limit exceeded for IP: {ip_address}',
                {'requests_per_minute': self.requests_per_minute}
            )
            return False
        
        # Add current request
        self.requests[ip_address].append((now, 1))
        return True


# Global rate limiter instance
rate_limiter = RateLimiter(requests_per_minute=60)


def rate_limit(f):
    """
    Decorator for rate limiting API endpoints.
    
    Usage:
        @rate_limit
        def my_endpoint():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip_address = request.remote_addr
        
        if not rate_limiter.is_allowed(ip_address):
            from flask import jsonify
            return jsonify({
                'error': 'Rate limit exceeded',
                'message': 'Too many requests. Please try again later.'
            }), 429
        
        return f(*args, **kwargs)
    
    return decorated_function


def log_request_duration(f):
    """
    Decorator to log API request duration.
    
    Usage:
        @log_request_duration
        def my_endpoint():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        start_time = time.time()
        
        try:
            response = f(*args, **kwargs)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Get status code
            if isinstance(response, tuple):
                status_code = response[1] if len(response) > 1 else 200
            else:
                status_code = 200
            
            # Log API access
            AuditLogger.log_api_access(
                endpoint=request.endpoint or 'unknown',
                method=request.method,
                status_code=status_code,
                duration_ms=duration_ms
            )
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            AuditLogger.log_api_access(
                endpoint=request.endpoint or 'unknown',
                method=request.method,
                status_code=500,
                duration_ms=duration_ms
            )
            raise
    
    return decorated_function


class InputValidator:
    """
    Enhanced input validation for security hardening.
    """
    
    @staticmethod
    def validate_image_id(image_id: str) -> bool:
        """
        Validate image ID format.
        
        Args:
            image_id: Image identifier
        
        Returns:
            bool: True if valid
        
        Raises:
            ValueError: If invalid
        """
        if not image_id:
            raise ValueError("Image ID cannot be empty")
        
        if len(image_id) < 8 or len(image_id) > 100:
            raise ValueError("Image ID must be 8-100 characters")
        
        # Check for SQL injection attempts
        dangerous_chars = ["'", '"', ';', '--', '/*', '*/', 'DROP', 'DELETE', 'UPDATE']
        for char in dangerous_chars:
            if char.lower() in image_id.lower():
                AuditLogger.log_security_event(
                    'sql_injection_attempt',
                    'high',
                    f'Potential SQL injection in image_id: {image_id}'
                )
                raise ValueError("Invalid characters in image ID")
        
        return True
    
    @staticmethod
    def validate_model_id(model_id: str) -> bool:
        """
        Validate model ID format.
        
        Args:
            model_id: Model identifier
        
        Returns:
            bool: True if valid
        
        Raises:
            ValueError: If invalid
        """
        if not model_id:
            raise ValueError("Model ID cannot be empty")
        
        if len(model_id) < 3 or len(model_id) > 100:
            raise ValueError("Model ID must be 3-100 characters")
        
        # Alphanumeric, hyphens, underscores only
        if not all(c.isalnum() or c in ['-', '_', '.'] for c in model_id):
            raise ValueError("Model ID contains invalid characters")
        
        return True
    
    @staticmethod
    def validate_timestamp(timestamp: str) -> bool:
        """
        Validate ISO 8601 timestamp format.
        
        Args:
            timestamp: Timestamp string
        
        Returns:
            bool: True if valid
        
        Raises:
            ValueError: If invalid
        """
        if not timestamp:
            raise ValueError("Timestamp cannot be empty")
        
        try:
            datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return True
        except ValueError:
            raise ValueError("Invalid timestamp format (use ISO 8601)")
    
    @staticmethod
    def validate_base64_image(image_b64: str, max_size_mb: int = 10) -> bool:
        """
        Validate base64-encoded image.
        
        Args:
            image_b64: Base64-encoded image
            max_size_mb: Maximum image size in MB
        
        Returns:
            bool: True if valid
        
        Raises:
            ValueError: If invalid
        """
        if not image_b64:
            raise ValueError("Image data cannot be empty")
        
        # Check size (base64 is ~33% larger than binary)
        max_size_bytes = max_size_mb * 1024 * 1024 * 1.33
        if len(image_b64) > max_size_bytes:
            AuditLogger.log_security_event(
                'oversized_upload',
                'medium',
                f'Image upload exceeds {max_size_mb}MB limit'
            )
            raise ValueError(f"Image exceeds {max_size_mb}MB size limit")
        
        # Check for valid base64 characters
        import string
        valid_chars = string.ascii_letters + string.digits + '+/='
        if not all(c in valid_chars for c in image_b64):
            raise ValueError("Invalid base64 encoding")
        
        return True


# Convenience functions
def validate_registration_input(data: Dict) -> None:
    """
    Validate registration request input.
    
    Args:
        data: Request data
    
    Raises:
        ValueError: If validation fails
    """
    InputValidator.validate_base64_image(data.get('image', ''))
    InputValidator.validate_model_id(data.get('model_id', ''))
    InputValidator.validate_timestamp(data.get('timestamp', ''))


def validate_verification_input(data: Dict) -> None:
    """
    Validate verification request input.
    
    Args:
        data: Request data
    
    Raises:
        ValueError: If validation fails
    """
    InputValidator.validate_base64_image(data.get('image', ''))
