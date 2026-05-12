# Provena

**Forensic provenance for AI-generated images.**

Drop in any image, get a **Photo Receipt** back — a set of seven independently-verifiable signals describing what the system can and cannot say about that image, plus a novel forensic primitive called **Echo Signature** that measures the post-registration processing channel an image has traveled through.

---

## What is the Photo Receipt?

The Photo Receipt is the main product surface. Every signal is independent — they are evidence, not a single up/down verdict — and each is honest about what it can and cannot prove.

| # | Signal | What it answers |
|---|--------|------------------|
| 1 | `manifest` | Is there a Provena/C2PA-shaped Ed25519-signed manifest in the EXIF, and does it verify against this image's perceptual hash? |
| 2 | `watermark` | Did TrustMark extract a valid 48-bit payload, and does that payload resolve to a record in our registry? |
| 3 | `edit_trace` | **Novel.** Echo Signature — a quantitative measurement of the channel the image traveled through since registration. |
| 4 | `registry` | Does the image's pHash match anything in our registry (exact, near-duplicate, or absent)? |
| 5 | `exif` | What does the EXIF say about camera, time, GPS, software, copyright? |
| 6 | `jpeg` | Are the JPEG quantization tables standard (camera-direct) or custom (editor re-save)? |
| 7 | `image` | Dimensions, format, color mode, file size, perceptual hash. |

Signals report `verified`, `found`, `absent`, `warning`, or `unknown` — never invented labels.

---

## The novel contribution: Echo Signature

Existing watermark systems (TrustMark, SynthID, Stable Signature) **correct** bit errors and discard the error pattern as noise. Echo inverts the framing: the watermark is the **probe**, and the bit-error pattern is the **measurement** of the unknown channel the image traveled through.

### Mechanism

At registration, the DCT layer embeds a 48-bit payload at known coefficient positions in the green channel. At verification, we read both:

- **Hard bits** — 0/1 thresholded decisions.
- **Soft signal** — signed distance from the QIM decision boundary, normalized to `[-1, +1]`.

Comparing the extracted hard bits to the registered payload gives a bit-error pattern. From that pattern plus the soft signal we compute a six-component **Echo Vector**:

| Component | Meaning |
|-----------|---------|
| `bit_error_rate` | Hard-bit BER — channel damage magnitude |
| `spatial_entropy` | Shannon entropy of error positions across a 4×4 image grid (bits) |
| `spectral_low_pct` | Fraction of error energy in low DCT frequencies (blur signature) |
| `spectral_high_pct` | Fraction in high frequencies (compression / sharpen) |
| `soft_mean_abs` | Mean of \|soft signal\| — strength of surviving signal |
| `soft_std` | Std of the soft signal — mixed vs uniform channel |

Each component is quantized to 8 bits. The six bytes are encoded directly as 12 hex characters — **not** SHA-256-hashed:

```
[ber][entropy][low][high][soft_mean][soft_std]
 2x   2x      2x   2x    2x         2x         = 12 hex chars
```

The critical design choice is plain hex encoding, which preserves L1 distance in feature space. Two images with similar processing histories produce Hamming-close fingerprints — cross-image similarity is a real semantic metric, not a hash-collision rate.

### Validated experimentally

| Comparison | Similarity |
|------------|------------|
| Same channel, different image: clean A vs clean B | **0.996** |
| Same channel, different image: q=75 A vs q=75 B | **0.982** |
| Same channel, different image: blur r=3 A vs blur r=3 B | **0.983** |
| Different channel, same image: clean A vs q=75 A | **0.536** |
| Different channel, same image: q=75 A vs blur r=1 A | **0.928** |

Every same-channel pair scored above 0.97; every different-channel pair scored below 0.93. 10/10 unique fingerprints across a 10-channel sweep, with the first 8 hex chars encoding the channel and the last 4 encoding image-specific finishing touches. Reproducible (same input → same output).

### Where the code lives

```
provena_flask/services/signals/echo_probe.py        # math primitive
provena_flask/services/signals/edit_trace_probe.py  # Photo Receipt wrapper
provena_flask/services/echo_registry.py             # persistence + similarity search
migrations/004_echo_fingerprints.sql                # PostgreSQL schema
```

### Honest limitations

- **DCT layer saturation.** Between JPEG q≈75 and q≈30, BER hard-clips to roughly the same value because coefficient (4,3) collapses to ~0. Soft moments separate them but only weakly. This fragility is **intentional** — it's what makes the layer a useful channel probe. Don't "fix" it.
- **Channel dominates the prefix.** Image content contributes to the last 4 hex chars; the channel dominates the first 8. Cross-image clustering works via the similarity function, not exact-prefix matching.
- **Needs ground truth.** Echo requires the canonical registered payload. On unregistered images, `edit_trace` correctly reports `absent`.

---

## Architecture

```
provena_flask/
├── __init__.py                     # app factory; /health probes DB + signing key
├── auth.py                         # API keys, SHA-256 storage, persisted daily rate limits
├── config.py                       # env-keyed config; HAMMING_MAX = 10
├── errors.py                       # {"error": {code, message, request_id}} envelope
├── blueprints/
│   ├── api.py                      # /api/v1/* — main API surface
│   ├── demo.py                     # /  → landing.html,  /app → demo.html
│   └── (registry, watermark, verification, reports)  # legacy stubs
├── models/
│   ├── db.py                       # SQLite/Postgres: pHash search, idempotency cache,
│   │                               #   api_usage, echo_fingerprints
│   └── provenance.py               # legacy dataclass
├── services/
│   ├── c2pa_service.py             # Ed25519 manifest + EXIF embed (pHash-bound)
│   ├── neural_watermark_service.py # TrustMark wrapper (lazy-loaded)
│   ├── hydra_watermark.py          # runs all 3 watermark layers at embed time
│   ├── payload_codec.py            # 4B record_id + 2B Reed-Solomon ECC = 6 bytes
│   ├── echo_registry.py            # Echo fingerprint persistence + similarity
│   ├── layers/
│   │   ├── neural_layer.py         # TrustMark
│   │   ├── dct_layer.py            # QIM at coeff (4,3), GREEN channel — channel probe
│   │   └── spatial_layer.py        # LSB with dim-seeded PRNG (fragile)
│   └── signals/                    # Photo Receipt orchestration
│       ├── _inspect.py             # runs all 7 probes in display order
│       ├── manifest_probe.py
│       ├── watermark_probe.py
│       ├── edit_trace_probe.py     # Echo Signature wrapper
│       ├── echo_probe.py           # Echo math primitive
│       ├── registry_probe.py
│       ├── exif_probe.py
│       ├── jpeg_probe.py
│       └── image_facts.py
└── templates/
    ├── landing.html                # /  — Kickstarter-style pitch
    └── demo.html                   # /app — Photo Receipt UI
```

### Watermark layers

Three layers are run at embed time; verification is **registry-aware** — each layer's payload is tried independently against the registry and the first hit wins. The old majority-vote scheme has been removed (DCT/LSB could outvote TrustMark with junk).

| Layer | Role | Notes |
|-------|------|-------|
| Neural (TrustMark) | Primary watermark for verification | Lazy-loaded, ~5 s first call, ~700 MB weights |
| DCT | Channel probe (Echo Signature) | Intentionally fragile under JPEG ≤ q=80 |
| Spatial (LSB) | Diagnostic only | Dies under any resize |

### Migrations

```
001_initial_schema.sql           # provenance_records, manifests, api_keys
002_audit_log.sql                # verification_log (append-only)
003_usage_and_idempotency.sql    # api_usage, idempotency_cache, idx_prov_payload_hex
004_echo_fingerprints.sql        # Echo Signature persistence
```

SQLite bootstrap creates all of these inline in `db.py`.

---

## API surface

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/api/v1/register` | ✅ | Stamp image with TrustMark + C2PA-shaped manifest |
| `POST` | `/api/v1/verify` | ✅ | 3-tier verify: manifest → watermark → pHash |
| `POST` | `/api/v1/inspect` | ✅ | **Photo Receipt** — main product surface |
| `POST` | `/api/v1/keys` | open | Issue `prov_sk_*` API keys |
| `DELETE` | `/api/v1/keys/<id>` | ✅ | Soft-delete (revoke) |
| `GET` | `/api/v1/manifests/<id>` | ✅ | |
| `GET` | `/api/v1/records/<id>` | ✅ | (`payload_hex` truncated) |
| `GET` | `/api/v1/usage` | ✅ | Per-key daily quotas |
| `GET` | `/api/v1/status` | open | Health / version |
| `POST` | `/api/v1/images/register` | open | Legacy proxy used by the bundled demo UI |
| `POST` | `/api/v1/images/verify` | open | Legacy |
| `POST` | `/api/v1/images/inspect` | open | Legacy — used by bundled demo UI |
| `GET` | `/health` | open | Probes DB + signing key |
| `GET` | `/` | open | Landing page |
| `GET` | `/app` | open | Photo Receipt UI |

All errors return a standardized envelope:

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```

---

## What Provena does NOT claim

- **Not C2PA-compliant.** The system emits C2PA-*shaped* JSON-with-signature, not output from the official `c2pa-python` SDK. Real C2PA verifiers won't accept it.
- **Not an AI-image detector.** Classifier-based detectors don't generalize across models; Provena measures provenance and channel, not "is this AI."
- **Not a single source of truth.** The registry is local. Federated cross-attestation (the Provenance Mesh) is sketched but not built.
- **Not a forensic verdict engine.** The heuristic `forensic_analyzer` was found to false-positive on every natural image and is disabled. The Photo Receipt reports independent signals; humans interpret.

---

## Setup

### Prerequisites

- Python 3.13
- ~1 GB free for TrustMark model weights (downloaded on first call)

### One-time setup

```bash
python3 -m venv /tmp/provena-venv
/tmp/provena-venv/bin/pip install -r requirements.txt
```

The first TrustMark invocation downloads ~700 MB of weights to:

```
/tmp/provena-venv/lib/python3.13/site-packages/trustmark/models/
```

That call takes ~5 s; subsequent calls are fast.

### Optional: enable manifest signing

Without an Ed25519 key pair, manifests are emitted unsigned and `/health` reports `signing_key: missing` (a warning is logged). To enable signing, drop:

```
keys/default_private.pem
keys/default_public.pem
```

Both files are `.gitignore`d.

---

## Boot the server

Port 5000 conflicts with macOS AirPlay Receiver — use 5010+ for dev.

```bash
SQLITE_PATH=/tmp/provena_dev.db /tmp/provena-venv/bin/python -c "
import os
os.environ.setdefault('FLASK_ENV', 'development')
from provena_flask import create_app
app = create_app('development')
app.run(host='127.0.0.1', port=5020, debug=False, use_reloader=False)
"
```

Then visit:

- <http://localhost:5020/> — landing page
- <http://localhost:5020/app> — Photo Receipt UI

---

## Verify the Echo Signature end-to-end

```python
import base64, io, requests
from PIL import Image, ImageFilter
import numpy as np

API = "http://localhost:5020"

def make_image(seed, size=512):
    rng = np.random.default_rng(seed)
    arr = np.zeros((size, size, 3), dtype=np.float32)
    for o in (4, 8, 16, 32, 64):
        s = max(1, size // o)
        c = rng.normal(0, 60, (o, o, 3))
        arr += np.repeat(np.repeat(c, s, axis=0), s, axis=1)[:size, :size] / np.log2(o + 1)
    arr = (255 * (arr - arr.min()) / (arr.max() - arr.min())).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr)

def to_png(img):
    b = io.BytesIO(); img.save(b, format="PNG"); return b.getvalue()

def to_jpg(img, q):
    b = io.BytesIO(); img.save(b, format="JPEG", quality=q); return b.getvalue()

img = make_image(2026)
r = requests.post(f"{API}/api/v1/images/register", json={
    "image": base64.b64encode(to_png(img)).decode(),
    "model_id": "echo-test",
    "timestamp": "2026-05-12T00:00:00Z",
}, timeout=300).json()
stamped = base64.b64decode(r["watermarked_image"])
stamped_img = Image.open(io.BytesIO(stamped)).convert("RGB")

for label, b in [
    ("clean",     stamped),
    ("JPEG q=75", to_jpg(stamped_img, 75)),
    ("blur r=2",  to_png(stamped_img.filter(ImageFilter.GaussianBlur(radius=2)))),
]:
    r = requests.post(f"{API}/api/v1/images/inspect",
                      json={"image": base64.b64encode(b).decode()},
                      timeout=180).json()
    et = next(s for s in r["signals"] if s["key"] == "edit_trace")
    ev = et["evidence"]
    print(f"{label:12} BER={ev['bit_error_rate']}  soft_μ={ev['soft_mean_abs']}  fp={ev['fingerprint']}")
```

Expected shape (exact numbers vary per machine):

```
clean        BER=  0.0%  soft_μ=0.7xx  fp=00000000xxxx
JPEG q=75    BER=4x–5x%  soft_μ=0.9xx  fp=<8-char channel prefix><4-char image suffix>
blur r=2     BER=4x–5x%  soft_μ=0.5–0.6  fp=<distinguishable from JPEG>
```

If `edit_trace` returns `status: absent`, the watermark didn't survive extraction — confirm TrustMark is installed and you're inspecting the **stamped** image, not the original.

---

## Tests

```bash
/tmp/provena-venv/bin/python -m pytest tests/unit -v
```

Last known: 42 passed, 4 honestly-skipped (recompression detection deferred).

---

## Open threads

- **Provenance Mesh** — federated cross-attestation via daily Merkle roots. Sketched, not built. Would close the C2PA single-source-of-truth gap; needs a multi-node demo.
- **Real C2PA SDK** — `c2pa_service.py` currently emits custom JSON-with-signature; productization should swap in `c2pa-python`.
- **Echo Signature classifier** — currently outputs raw quantitative facts. A small logistic regression on labeled data could map `(BER, entropy, spectral, soft moments)` → edit-type labels (`jpeg-q-N`, `blur-r-N`, `crop-X%`). Needs a synthesized labeled dataset.
- **Onboarding flow** — the landing CTA goes to `/app`, but users arrive with no API key. The demo currently uses the unauthenticated legacy `/api/v1/images/*` endpoints. A "create key" wizard is the next product step.
- **Workshop writeup** — the Echo Signature primitive merits a paper-length writeup.

---

## Documentation

- [README.md](README.md) — this file
- [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) — architecture, verification flow, threat model
- [DEMO_GUIDE.md](DEMO_GUIDE.md) — demo scenarios and talking points
- `CLAUDE.md` / `CHANGELOG.md` — agent-facing handoff and per-phase audit trail (when present)

---

## Acknowledgments

Built on:

- **Flask** — web framework
- **TrustMark** — neural watermarking
- **cryptography** — Ed25519 signatures
- **OpenCV**, **Pillow**, **PyWavelets** — image processing
- **SQLite** / **PostgreSQL** — registry + Echo fingerprint store

Related work (key papers):

- Lin (2009) — *Digital Image Source Coder Forensics Via Intrinsic Fingerprints* (pixel-level coder forensics; not watermark-based)
- C2PA technical specification
- TrustMark, SynthID, Stable Signature — robustness-focused watermark systems
- arXiv 2510.05978, 2511.05598 — diffusion-based watermark removal
- arXiv 2502.19567 — Atlas: federated provenance for ML pipelines

---

**Provena**: forensic image provenance with a novel channel-measurement primitive — Echo Signature.
