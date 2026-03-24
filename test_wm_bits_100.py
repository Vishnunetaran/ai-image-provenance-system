import numpy as np
from PIL import Image
from imwatermark import WatermarkEncoder, WatermarkDecoder
import secrets

def test_wm():
    n_bits = 100
    payload_bits = np.random.choice([0, 1], size=n_bits)
    
    img = Image.new('RGB', (512, 512), color=(128, 128, 128))
    img_np = np.array(img)[:, :, ::-1].copy() # BGR
    
    encoder = WatermarkEncoder()
    encoder.set_watermark('bits', payload_bits)
    wm_img_bgr = encoder.encode(img_np, 'dwtDctSvd')
    
    decoder = WatermarkDecoder('bits', n_bits)
    extracted_bits = decoder.decode(wm_img_bgr, 'dwtDctSvd')
    
    bit_errors = np.sum(payload_bits != extracted_bits)
    print(f"Bit errors: {bit_errors} / {n_bits}")

if __name__ == '__main__':
    test_wm()
