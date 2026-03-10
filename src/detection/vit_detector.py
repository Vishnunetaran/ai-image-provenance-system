"""
ViT-Small Global Pattern Detector.

Vision Transformer-based detector that focuses on global composition
patterns to identify AI-generated images.  ViTs excel at capturing
long-range dependencies that CNNs miss, making them complementary to
the EfficientNet-based classifier.

Architecture:
    ViT-Small (timm) → [CLS] token → MLP → sigmoid

References:
    - ViT (Dosovitskiy et al., 2020)
    - Detection of AI-Generated Images survey (2024)
"""

import logging
from typing import Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
except ImportError:
    raise ImportError("PyTorch required: pip install torch")

try:
    import timm
except ImportError:
    timm = None

try:
    import cv2
except ImportError:
    cv2 = None


class ViTDetector:
    """ViT-Small AI image detector.

    Uses a Vision Transformer to detect global patterns characteristic
    of AI-generated images.  Focuses on composition, texture consistency,
    and frequency artefacts that are spread across the entire image.

    Args:
        model_path: Path to trained model weights (optional).
        device: Torch device.
        threshold: Detection threshold (default 0.5).

    Example:
        >>> detector = ViTDetector()
        >>> image = cv2.imread("test.png")
        >>> is_ai, confidence = detector.predict(image)
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: Optional[str] = None,
        threshold: float = 0.5,
    ):
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self.threshold = threshold
        self.image_size = 224  # ViT-Small default

        self.model = self._build_model()
        self.model = self.model.to(self.device)

        if model_path is not None:
            self._load_weights(model_path)

        self.model.eval()

        logger.info(
            f"ViTDetector initialised: device={self.device}, "
            f"threshold={threshold}"
        )

    def _build_model(self) -> nn.Module:
        """Build ViT-Small binary classifier."""
        if timm is not None:
            model = timm.create_model(
                "vit_small_patch16_224",
                pretrained=True,
                num_classes=1,
            )
        else:
            # Minimal ViT-like architecture for fallback
            model = nn.Sequential(
                nn.Conv2d(3, 64, 16, stride=16),  # Patch embedding
                nn.Flatten(2),  # [B, 64, N]
                nn.Linear(64, 1),
            )
            logger.warning("timm not available; using minimal fallback")

        return model

    def _load_weights(self, path: str):
        """Load trained model weights."""
        try:
            state_dict = torch.load(path, map_location=self.device)
            if "model_state" in state_dict:
                state_dict = state_dict["model_state"]
            self.model.load_state_dict(state_dict)
            logger.info(f"Loaded ViT weights from {path}")
        except Exception as e:
            logger.warning(f"Could not load weights from {path}: {e}")

    def _preprocess(self, image: np.ndarray) -> torch.Tensor:
        """Preprocess image for ViT input.

        Args:
            image: BGR numpy image (H, W, 3).

        Returns:
            Normalised tensor (1, 3, 224, 224).
        """
        if cv2 is not None:
            img = cv2.resize(image, (self.image_size, self.image_size))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        else:
            img = image

        tensor = torch.tensor(img, dtype=torch.float32).permute(2, 0, 1) / 255.0
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        tensor = (tensor - mean) / std

        return tensor.unsqueeze(0).to(self.device)

    @torch.no_grad()
    def predict(self, image: np.ndarray) -> Tuple[bool, float]:
        """Detect whether an image is AI-generated.

        Args:
            image: Input image (BGR numpy array).

        Returns:
            Tuple of (is_ai_generated, confidence).
        """
        self.model.eval()
        tensor = self._preprocess(image)

        output = self.model(tensor)

        # Handle different output shapes
        if output.dim() > 1:
            logit = output.view(-1)[0]
        else:
            logit = output

        confidence = torch.sigmoid(logit).item()
        is_ai = confidence >= self.threshold

        logger.debug(f"ViT detection: ai={is_ai}, conf={confidence:.3f}")
        return is_ai, confidence

    @torch.no_grad()
    def predict_batch(self, images: list) -> list:
        """Detect a batch of images.

        Args:
            images: List of BGR numpy images.

        Returns:
            List of (is_ai, confidence) tuples.
        """
        self.model.eval()
        tensors = torch.cat([self._preprocess(img) for img in images], dim=0)
        outputs = self.model(tensors)
        confidences = torch.sigmoid(outputs).squeeze().cpu().numpy()

        if confidences.ndim == 0:
            confidences = [float(confidences)]
        else:
            confidences = confidences.tolist()

        return [(c >= self.threshold, c) for c in confidences]

    def get_model(self) -> nn.Module:
        """Get the underlying PyTorch model (for training)."""
        return self.model
