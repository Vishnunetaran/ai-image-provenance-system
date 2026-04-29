# Provena V2 - Architectural Flow

Provena V2 is an AI Image Provenance System designed to seamlessly attach verifiable origin metadata into AI-generated images using a triple-redundant fallback approach.

## 1. Top-Level Flow

```mermaid
graph TD
    User([User Application]) -->|Image + Metadata| Auth[API Authentication]
    Auth --> Register[Registration Pipeline]
    Auth --> Verify[Verification Pipeline]

    subgraph Registration [/api/v1/images/register]
    P_HASH[Calculate pHash] --> DB_INSERT[(Save to DB)]
    DB_INSERT --> NEURAL[Adobe TrustMark Embed]
    NEURAL --> C2PA[C2PA Header Injection]
    end

    subgraph Verification [/api/v1/images/verify]
    C2PA_CHECK{C2PA Valid?} 
    C2PA_CHECK -- Yes --> V1((Verified))
    C2PA_CHECK -- No / Stripped --> NEURAL_CHECK{TrustMark Extracted?}
    
    NEURAL_CHECK -- Yes + DB Match --> V2((Verified))
    NEURAL_CHECK -- No / Blocked --> PHASH_CHECK{pHash D<=10?}
    
    PHASH_CHECK -- Yes --> V3((Verified Modified))
    PHASH_CHECK -- No --> PHASH_WIDE{pHash D<=20?}
    
    PHASH_WIDE -- Yes --> V4((Verified Modified Wide))
    PHASH_WIDE -- No --> U((Unregistered))
    end
```

## 2. Core Subsystems

### A. Authentication & Registration
All requests require a valid Enterprise API key passed as `Bearer Token`.
1. Once authenticated, the system accepts an image.
2. It generates a 64-bit fingerprint of the raw pixels using `imagehash.phash`.
3. It creates a Database UUID record for the payload.
4. **Important**: The UUID is mathematically reduced using Reed-Solomon to exactly **48 bits** as a `payload_codec` standard.

### B. Adobe TrustMark (The Neural Layer)
This is the heart of the system's robustness:
- The 48-bit string is injected completely invisibly into the pixel variance of the image using Adobe's ResNet50 `Q-variant` Neural Network. 
- The payload represents `BCH error-correction` parameters ensuring even if the image is resized, JPEG compressed, or heavily cropped, TrustMark can probabilistically detect the hidden UUID bits from the remaining pixels.

### C. The Verification Cascade
The `/verify` endpoint operates sequentially to guarantee traceability:
- **Level 1 (C2PA/Metadata):** Extremely fast, strictly cryptographic verification of file headers. Dies instantly if posted to Instagram/X (Twitter) due to automatic EXIF stripping.
- **Level 2 (Neural/TrustMark):** Operates on the RGB pixels themselves. Mathematically decodes the robust 48-bit payload to find the original UUID in the database.
- **Level 3 (Strict pHash):** Used if the Neural watermark was destroyed. Looks for near identical Hamming distance images in the DB (`distance <= 10`).
- **Level 4 (Wide pHash):** Used as a final resort for heavy meme formatting or drastic physical cropping (`distance <= 20`).

### D. The Database
Provena V2 supports both PostgreSQL (with pgvector for super-fast bit-count distance searching) and pure Python SQLite.
Table setup:
- `api_keys`: Contains authorization metrics and rate-limits.
- `provenance_records`: Holds the `record_id`, generator model details, and the 64-bit integer format of the Image pHash for native fast comparisons.
- `manifests`: Secure localized copies of all parsed C2PA schemas.
