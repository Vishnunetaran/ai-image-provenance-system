"""
Unit tests for the SIFT and Homography-based feature_anchor.py service.

Tests:
  - SIFT feature registration and retrieval
  - Skipping flat/featureless images
  - Matching the exact same image
  - Matching cropped image fragments (translation/scale check)
  - Matching rotated query images (rotation invariance check)
  - Rejecting completely unrelated images
"""
import os
import pytest
from PIL import Image, ImageDraw

from provena_flask.models import db
from provena_flask.services import feature_anchor


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """Use a temporary SQLite DB for unit testing."""
    db_path = str(tmp_path / "test.db")
    os.environ["SQLITE_PATH"] = db_path
    db.SQLITE_PATH = db_path
    db.run_migrations()
    
    # Ensure cache is clean
    feature_anchor._SIFT_CACHE.clear()
    feature_anchor._CACHE_LOADED = False
    
    yield
    
    # Cleanup database file
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


@pytest.fixture
def test_image() -> Image.Image:
    """Create a high-contrast textured image with distinct shapes so SIFT detects many keypoints."""
    img = Image.new("RGB", (512, 512), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    # Draw geometric texture patterns
    for i in range(10, 500, 25):
        draw.line([(i, 0), (512 - i, 512)], fill=(i % 255, (i*2)%255, (i*3)%255), width=3)
        draw.line([(0, i), (512, 512 - i)], fill=((i*2)%255, i%255, (i*4)%255), width=3)
        
    # Draw solid distinct forms (square, circle)
    draw.rectangle([120, 120, 220, 220], fill=(0, 255, 0), outline=(255, 0, 0), width=4)
    draw.ellipse([280, 120, 420, 260], fill=(0, 0, 255), outline=(255, 255, 0), width=4)
    
    return img


@pytest.fixture
def featureless_image() -> Image.Image:
    """Create a solid gray image with no SIFT features."""
    return Image.new("RGB", (512, 512), (128, 128, 128))


class TestFeatureAnchorSIFT:
    """Tests SIFT registration, matching, and validation logic."""

    def test_register_and_retrieve_sift_features(self, test_image):
        """register_features must extract features, write to DB, and populate cache."""
        record_id = "test_record_1"
        res = feature_anchor.register_features(record_id, test_image)
        
        assert res["status"] == "registered"
        assert res["record_id"] == record_id
        assert res["patches_stored"] > 10
        assert res["feature_dim"] == 128
        
        # Verify it loaded in the active cache
        assert record_id in feature_anchor._SIFT_CACHE
        kp, desc = feature_anchor._SIFT_CACHE[record_id]
        assert len(kp) == res["patches_stored"]
        assert desc.shape == (len(kp), 128)

    def test_featureless_image_skips_registration(self, featureless_image):
        """register_features must skip registration for low-entropy or blank images."""
        record_id = "flat_record"
        res = feature_anchor.register_features(record_id, featureless_image)
        
        assert res["status"] == "skipped"
        assert res["patches_stored"] == 0
        assert "reason" in res

    def test_exact_match(self, test_image):
        """Matching the exact registered image must return matched=True and low reprojection error."""
        record_id = "exact_match_record"
        feature_anchor.register_features(record_id, test_image)
        
        match_res = feature_anchor.match_fragment(test_image)
        
        assert match_res.matched is True
        assert match_res.record_id == record_id
        assert match_res.confidence >= 0.90
        assert match_res.homography is not None
        
        # Region boundary projections should align with original canvas dimensions (0,0) to (512,512)
        x1, y1, x2, y2 = match_res.matched_region
        assert abs(x1 - 0) <= 5
        assert abs(y1 - 0) <= 5
        assert abs(x2 - 512) <= 5
        assert abs(y2 - 512) <= 5

    def test_cropped_image_match(self, test_image):
        """Cropped center region of a registered image must match successfully and project correct coordinates."""
        record_id = "crop_match_record"
        feature_anchor.register_features(record_id, test_image)
        
        # Crop 256x256 center region
        crop_box = (128, 128, 384, 384)
        cropped_img = test_image.crop(crop_box)
        
        match_res = feature_anchor.match_fragment(cropped_img)
        
        assert match_res.matched is True
        assert match_res.record_id == record_id
        assert match_res.homography is not None
        
        # Bounding box coordinates projected back to original space should match the crop box (128, 128, 384, 384)
        x1, y1, x2, y2 = match_res.matched_region
        assert abs(x1 - 128) <= 15
        assert abs(y1 - 128) <= 15
        assert abs(x2 - 384) <= 15
        assert abs(y2 - 384) <= 15

    def test_rotated_image_match(self, test_image):
        """Rotated images must be matched correctly due to SIFT's orientation invariance."""
        record_id = "rotated_match_record"
        feature_anchor.register_features(record_id, test_image)
        
        # Rotate image by 45 degrees
        rotated_img = test_image.rotate(45, resample=Image.Resampling.BICUBIC)
        
        match_res = feature_anchor.match_fragment(rotated_img)
        
        assert match_res.matched is True
        assert match_res.record_id == record_id
        assert match_res.homography is not None

    def test_unrelated_image_no_match(self, test_image):
        """Completely different texture images must return matched=False with zero homography inliers."""
        record_id = "target_image_record"
        feature_anchor.register_features(record_id, test_image)
        
        # Create an unrelated bullseye circle image
        unrelated_img = Image.new("RGB", (512, 512), (0, 0, 0))
        draw = ImageDraw.Draw(unrelated_img)
        for r in range(10, 240, 20):
            draw.ellipse([256-r, 256-r, 256+r, 256+r], outline=(255, 255, 255), width=3)
            
        match_res = feature_anchor.match_fragment(unrelated_img)
        
        assert match_res.matched is False
        assert match_res.record_id is None
        assert match_res.match_count < 12
