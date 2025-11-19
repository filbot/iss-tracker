
from PIL import Image
from iss_display.pipeline.image_preprocessor import FrameEncoder

def test_black_threshold():
    # Create an encoder with default settings
    encoder = FrameEncoder(
        width=8,
        height=1,
        has_red=False,
        logical_width=8,
        pad_left=0,
        pad_right=0,
        rotate_degrees=0,
        # We expect the default to be 128 after our change
    )
    
    # Create an image with a gray pixel that would be white with threshold 96 
    # but black with threshold 128.
    # (100, 100, 100) is gray. max(100, 100, 100) = 100.
    # 100 <= 96 is False (White)
    # 100 <= 128 is True (Black)
    
    canvas = Image.new("RGB", (8, 1), "white")
    canvas.putpixel((0, 0), (100, 100, 100)) # Dark gray
    
    red, black = encoder.encode(canvas)
    
    # black buffer: 0 means black pixel, 1 means white pixel.
    # We expect the first pixel (bit 7 of byte 0) to be 0 (black).
    # 0x7F is 0111 1111
    
    assert black[0] == 0x7F

def test_light_gray_is_white():
    encoder = FrameEncoder(
        width=8,
        height=1,
        has_red=False,
        logical_width=8,
        pad_left=0,
        pad_right=0,
        rotate_degrees=0,
    )
    
    # (150, 150, 150) is light gray. max = 150.
    # 150 <= 128 is False (White)
    
    canvas = Image.new("RGB", (8, 1), "white")
    canvas.putpixel((0, 0), (150, 150, 150))
    
    red, black = encoder.encode(canvas)
    
    # Expect all white (0xFF)
    assert black[0] == 0xFF
