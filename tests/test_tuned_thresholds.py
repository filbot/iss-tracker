
from PIL import Image
from iss_display.pipeline.image_preprocessor import FrameEncoder

def test_tuned_thresholds():
    encoder = FrameEncoder(
        width=8,
        height=1,
        has_red=True,
        logical_width=8,
        pad_left=0,
        pad_right=0,
        rotate_degrees=0,
        # Defaults: black=170, red_min=130, red_g/b_max=110
    )
    
    canvas = Image.new("RGB", (8, 1), "white")
    
    # 1. Dark Gray (160, 160, 160) -> Should be Black (<= 170)
    canvas.putpixel((0, 0), (160, 160, 160))
    
    # 2. Light Gray (180, 180, 180) -> Should be White (> 170)
    canvas.putpixel((1, 0), (180, 180, 180))
    
    # 3. Anti-aliased Red (200, 100, 100) -> Should be Red
    # r=200 >= 130, g=100 <= 110, b=100 <= 110, diff=100 >= 30
    canvas.putpixel((2, 0), (200, 100, 100))
    
    # 4. Dark Red (100, 0, 0) -> Should be Black (not red because r < 130)
    # max=100 <= 170 -> Black
    canvas.putpixel((3, 0), (100, 0, 0))
    
    red, black = encoder.encode(canvas)
    
    # Check Pixel 0 (Dark Gray) -> Black
    # Red bit 7 should be 1 (white/unset), Black bit 7 should be 0 (set)
    assert (red[0] & 0x80) == 0x80
    assert (black[0] & 0x80) == 0x00
    
    # Check Pixel 1 (Light Gray) -> White
    # Red bit 6 should be 1, Black bit 6 should be 1
    assert (red[0] & 0x40) == 0x40
    assert (black[0] & 0x40) == 0x40
    
    # Check Pixel 2 (AA Red) -> Red
    # Red bit 5 should be 0 (set), Black bit 5 should be 1 (unset)
    assert (red[0] & 0x20) == 0x00
    assert (black[0] & 0x20) == 0x20
    
    # Check Pixel 3 (Dark Red) -> Black
    # Red bit 4 should be 1, Black bit 4 should be 0
    assert (red[0] & 0x10) == 0x10
    assert (black[0] & 0x10) == 0x00
