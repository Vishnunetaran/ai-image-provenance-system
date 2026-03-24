import numpy as np
from PIL import Image
from imwatermark import WatermarkEncoder, WatermarkDecoder
import secrets

def test_wm():
    payload = secrets.token_bytes(32)
    payload_bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    
    img = Image.new('RGB', (512, 512), color=(255, 255, 255))
    img_np = np.array(img)[:, :, ::-1].copy() # BGR
    
    encoder = WatermarkEncoder()
    encoder.set_watermark('bits', payload_bits)
    wm_img_bgr = encoder.encode(img_np, 'dwtDctSvd')
    
    decoder = WatermarkDecoder('bits', 256)
    extracted_bits = decoder.decode(wm_img_bgr, 'dwtDctSvd')
    
    extracted_payload = np.packbits(extracted_bits).tobytes()
    
    print(f"Original:  {payload.hex()}")
    print(f"Extracted: {extracted_payload.hex()}")
    
    bit_errors = np.sum(payload_bits != extracted_bits)
    print(f"Bit errors: {bit_errors} / 256")

if __name__ == '__main__':
    test_wm()
