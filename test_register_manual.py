import requests
import base64
import numpy as np
from PIL import Image
import io

def test():
    # Create 256x256 image
    img = Image.new('RGB', (256, 256), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    
    url = "http://localhost:5000/api/v1/register"
    headers = {"Authorization": "Bearer test"}
    data = {"model_id": "test-model", "image": img_b64}
    
    resp = requests.post(url, headers=headers, json=data)
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.text}")

if __name__ == '__main__':
    test()
