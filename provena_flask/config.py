"""
Configuration module for Provena-FLASK.

Supports multiple environments: development, production, testing.
"""

import os
from pathlib import Path


class Config:
    """Base configuration class."""
    
    # Application settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Database settings
    BASE_DIR = Path(__file__).parent.parent
    DATABASE_PATH = './data/provenance.db'
    
    # Keys storage
    KEYS_DIR = './keys'
    
    # Watermark settings
    WATERMARK_PAYLOAD_BITS = 128
    WATERMARK_MIN_PSNR = 45.0
    WATERMARK_MIN_SSIM = 0.99
    
    # Perceptual hash settings
    PHASH_THRESHOLD = 10  # Hamming distance threshold
    
    # Logging
    LOG_LEVEL = 'INFO'
    
    # API settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max request size (Base64 can be large)
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    
    # Rate limiting (requests per minute)
    RATE_LIMIT_REGISTER = 10
    RATE_LIMIT_VERIFY = 30


class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True
    TESTING = False
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Production environment configuration."""
    DEBUG = False
    TESTING = False
    LOG_LEVEL = 'WARNING'
    
    def __init__(self):
        """Validate production-specific requirements."""
        super().__init__()
        secret_key = os.environ.get('SECRET_KEY')
        if not secret_key:
            raise ValueError("SECRET_KEY must be set in production environment")
        self.SECRET_KEY = secret_key


class TestingConfig(Config):
    """Testing environment configuration."""
    DEBUG = True
    TESTING = True
    DATABASE_PATH = ':memory:'  # Use in-memory database for tests


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
