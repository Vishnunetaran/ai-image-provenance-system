---
title: Provena
emoji: 🔒
colorFrom: red
colorTo: black
sdk: docker
app_port: 7860
pinned: false
---

# PROVENA-FLASK: HydraWatermark V4

**AI Image Provenance, SIFT-Homography & Multi-Layer Forensic Verification System**

PROVENA-FLASK is a state-of-the-art provenance tracking, forensic localization, and tamper-verification platform for AI-generated images. Moving beyond fragile metadata or easily degraded watermarks, PROVENA V4 combines Elliptic Curve cryptographic metadata signatures (C2PA) with a highly resilient, five-layer pixel watermarking engine (HydraWatermark V3), SIFT-RANSAC homography alignment, and an adversarial self-hardening feedback loop.

---

## Key Highlights in V3 & V4

1. **SIFT Keypoint & Homography Alignment**: Replaced legacy heuristic anchors with robust SIFT descriptors. If an image is cropped, scaled, rotated, or skewed, the system calculates the homography transform matrix $H$ and warps the query fragment back to the original registered coordinate space. This restores the pixel alignment necessary for downstream watermark extractors (DCT, Tiled DCT, Spatial) to succeed on heavy crops.
2. **Tiled Holographic Watermarking**: Slides a 128x128 window with 50% overlap, embedding the full 48-bit payload independently into every tile. Uses Barker-13 synchronization codes to detect tile boundaries on severely cropped fragments.
3. **Tamper Localization Grid**: Embeds cell-specific spatial payloads protected by CRC-8 checksums. Returns a pixel-perfect `tamper_map` showing exactly which parts of an image are authentic (green), altered (red), or represent unaligned crop margins (grey).
4. **Watermark-Feature ID Cross-Verification (Splicing Detection)**: Detects watermark transfer/replay attacks by validating that the record ID extracted from the watermark payload matches the parent record ID retrieved by the SIFT features. Mismatches trigger a `TAMPERED` verdict.
5. **Adversarial Self-Hardening Engine**: Tracks the last 1,000 verification outcomes in a rolling telemetry DB. Dynamically adjusts embedding strengths (quantization step size, spatial redundancy, tile overlap) based on attack classification (compression, noise, cropping).
6. **Relaxed size limits for verification**: Registration strictly enforces a minimum image size of `256×256px` to ensure robust watermark capacity. However, verification bypasses this check, allowing small fragments down to `32×32px` to be aligned and verified.

---

## Dual-Layered Forensic Truth

Most systems rely solely on file metadata (which is immediately stripped by chat applications and social media platforms) or single-layer watermarks (which fail under heavy compression or cropping). PROVENA implements a multi-layer verification hierarchy:

- **Tier 1 (Cryptographic)**: C2PA & Ed25519 signatures embedded in file EXIF data.
- **Tier 2 (Pixel-Proof)**: Five-Layer HydraWatermark (Neural, DCT, Tiled DCT, Spatial, and Tamper Grid) evaluated using a consensus majority-vote engine.
- **Tier 3 (Geometrical)**: SIFT Descriptor + RANSAC Homography validation.
- **Tier 4 (Perceptual)**: 64-bit coarse pHash index fallback.

---

## System Architecture

### Pipeline Flow

```mermaid
graph TD
    %% Registration Flow
    subgraph Registration Pipeline
        R_Input[Original Image] --> pHash[1. Calculate pHash]
        pHash --> C2PA_Embed[2. Inject C2PA Manifest]
        C2PA_Embed --> SIFT_Index[3. Index SIFT Keypoints]
        SIFT_Index --> Hydra_Embed[4. HydraWatermark V3 Embed]
        
        subgraph Hydra Embed
            H1[Neural TrustMark]
            H2[DCT Block QIM]
            H3[Tiled Holographic DCT]
            H4[Spatial LSB + ECC]
            H5[Tamper Grid + CRC-8]
        end
        Hydra_Embed --> R_Output[Final Watermarked Image]
    end
    
    %% Verification Flow
    subgraph Verification Pipeline
        V_Input[Query Image] --> C2PA_Check{1. C2PA Valid?}
        C2PA_Check -->|No| SIFT_RANSAC{2. SIFT Homography?}
        SIFT_RANSAC -->|Match & Warp| Hydra_Extract
        SIFT_RANSAC -->|No Match| pHash_Check{3. pHash Match?}
        
        V_Input --> Hydra_Extract[4. Hydra Extraction Layers]
        
        subgraph Hydra Extraction Layers
            E1[Neural TrustMark] --> Vote[Consensus Engine]
            E2[DCT Block QIM] --> Vote
            E3[Tiled Holographic DCT] --> Vote
            E4[Spatial LSB + ECC] --> Vote
            E5[Tamper Grid Validation] --> Vote
        end
        
        Vote --> Splicing_Check{5. SIFT ID == Watermark ID?}
        Splicing_Check -->|Mismatch| Verdict_Tamper[Verdict: SPLICED/TAMPERED]
        Splicing_Check -->|Match| Verdict[Verdict: VERIFIED]
        
        C2PA_Check -->|Valid| Splicing_Check
        pHash_Check -->|Match| Verdict_Modified[Verdict: VERIFIED_MODIFIED]
        pHash_Check -->|No Match| Verdict_Unreg[Verdict: UNREGISTERED]
    end
```

---

## Forensic Security Defenses

PROVENA V4 includes built-in algorithmic protections to survive adversarial environments:

* **DoS Keypoint Flooding Protection**: SIFT extractor caps keypoints to the top 1,000 using `nfeatures=1000`.
* **Degenerate Homography Filtering**: Homography matrices ($H$) must pass determinant checks ($10^{-4} \le \text{det}(H_{2\times2}) \le 10^4$) and ensure projected coordinates form a convex polygon (`cv2.isContourConvex`).
* **Ambiguous Pattern Rejection**: Rejects repetitive texture alignments using Lowe's Ratio Test ($0.75$).
* **Rigid Inlier Threshold**: Enforces a minimum of 12 geometrically consistent inliers with a tight error margin ($5.0$ pixel tolerance).
* **Flat/Low-Entropy Filter**: Skips feature alignment and registration on featureless/solid background images (evaluated in pixel-space standard deviation $\le 8.0$).
* **Defensive Tamper Mapping**: Blocks all-zero payload false positives on unwatermarked images and handles extreme noise cases gracefully.

---

## API Usage (Python)

All API endpoints are protected using secure key authentication (registered keys are dynamically rate-limited).

### 1. Register Image
```python
import requests
import base64

with open('image.png', 'rb') as f:
    image_b64 = base64.b64encode(f.read()).decode()

response = requests.post('http://localhost:5000/api/v1/images/register', 
    headers={'Authorization': 'Bearer prov_sk_YOUR_SECRET_KEY'},
    json={
        'image': image_b64,
        'model_id': 'gpt-vision-v1',
        'timestamp': '2026-06-03T00:00:00Z'
    }
)

result = response.json()
print(f"Record Registered: {result['record_id']}")
```

### 2. Verify Image
```python
response = requests.post('http://localhost:5000/api/v1/images/verify',
    headers={'Authorization': 'Bearer prov_sk_YOUR_SECRET_KEY'},
    json={
        'image': image_b64
    }
)

result = response.json()
print(f"Status: {result['status']}")
print(f"Layer Breakdown: {result['verification']['hydra_layers']}")
```

---

## Development & Test Automation

Run unit and integration tests using pytest:
```bash
python -m pytest tests/
```

To run the adversarial stress testing suite:
```bash
python -m provena_flask.services.adversarial_forge
```

To start the Flask development server:
```bash
python run.py
```
Then open `http://localhost:5000` to interact with the sleek **editorial red-and-black forensic UI**, featuring live blueprint homography mappings, interactive canvas-rendered tamper localization maps, and an animated particle constellation backdrop.

---

## Product Presentation & Sales Deck Prep
For business stakeholders, pitch scripts, detailed acronym tables (C2PA, SIFT, RANSAC, DCT, QIM, LSB, ECC, CRC-8, pHash, EMA), and a complete Q&A prep sheet, refer to the [Product Presentation Guide](file:///C:/Users/vishn/.gemini/antigravity/brain/ff07b7f6-5e2c-4d1b-9ce9-9b5a0384d48d/product_presentation_guide.md) generated in your app data brain space.

