import json
import base64
import requests
import os

# Create a small dummy image
from PIL import Image
import io
img = Image.new('RGB', (256, 256), color = (73, 109, 137))
img_byte_arr = io.BytesIO()
img.save(img_byte_arr, format='PNG')
img_bytes = img_byte_arr.getvalue()
img_b64 = base64.b64encode(img_bytes).decode('utf-8')

payload = {
    "image": img_b64,
    "model_id": "test_model",
    "timestamp": "2026-03-20T21:00:00Z"
}

try:
    r = requests.post("http://127.0.0.1:5000/api/v1/images/register", json=payload)
    print(f"Status: {r.status_code}")
    print(r.json())
except Exception as e:
    print(f"Error: {e}")
