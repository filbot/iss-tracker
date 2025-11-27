#!/usr/bin/env python3
"""
Debug script to trace color issues through the entire pipeline.
This will help identify exactly where colors are getting swapped.
"""

import numpy as np
from PIL import Image
import io

# Test 1: Create a simple test image with known colors
print("=" * 60)
print("TEST 1: PIL Image Color Order")
print("=" * 60)

# Create a 3x1 image with R, G, B pixels
img = Image.new('RGB', (3, 1))
img.putpixel((0, 0), (255, 0, 0))    # Red
img.putpixel((1, 0), (0, 255, 0))    # Green  
img.putpixel((2, 0), (0, 0, 255))    # Blue

# Convert to numpy and check order
arr = np.array(img)
print(f"PIL RGB image shape: {arr.shape}")
print(f"Pixel 0 (should be RED):   R={arr[0,0,0]}, G={arr[0,0,1]}, B={arr[0,0,2]}")
print(f"Pixel 1 (should be GREEN): R={arr[0,1,0]}, G={arr[0,1,1]}, B={arr[0,1,2]}")
print(f"Pixel 2 (should be BLUE):  R={arr[0,2,0]}, G={arr[0,2,1]}, B={arr[0,2,2]}")

# Test 2: RGB565 conversion
print("\n" + "=" * 60)
print("TEST 2: RGB565 Conversion")
print("=" * 60)

def rgb_to_rgb565(r, g, b):
    """Convert RGB888 to RGB565"""
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565

def rgb565_to_bytes(rgb565):
    """Convert RGB565 to two bytes (big endian)"""
    high = (rgb565 >> 8) & 0xFF
    low = rgb565 & 0xFF
    return high, low

# Known correct values for ST7796S:
# Pure RED   = 0xF800 = high:0xF8, low:0x00
# Pure GREEN = 0x07E0 = high:0x07, low:0xE0
# Pure BLUE  = 0x001F = high:0x00, low:0x1F

print("\nExpected values (from ST7796S datasheet):")
print("  RED:   0xF800 -> bytes: 0xF8, 0x00")
print("  GREEN: 0x07E0 -> bytes: 0x07, 0xE0")
print("  BLUE:  0x001F -> bytes: 0x00, 0x1F")

print("\nOur conversion results:")
for name, (r, g, b) in [("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255))]:
    rgb565 = rgb_to_rgb565(r, g, b)
    high, low = rgb565_to_bytes(rgb565)
    print(f"  {name}: RGB({r},{g},{b}) -> 0x{rgb565:04X} -> bytes: 0x{high:02X}, 0x{low:02X}")

# Test 3: Full array conversion (simulating what display() does)
print("\n" + "=" * 60)
print("TEST 3: Full Array Conversion (as in display())")
print("=" * 60)

# Create test image
test_img = Image.new('RGB', (3, 1))
test_img.putpixel((0, 0), (255, 0, 0))    # Red
test_img.putpixel((1, 0), (0, 255, 0))    # Green  
test_img.putpixel((2, 0), (0, 0, 255))    # Blue

img_np = np.array(test_img)
print(f"Input array shape: {img_np.shape}")
print(f"Input array:\n{img_np}")

r = img_np[..., 0]
g = img_np[..., 1]
b = img_np[..., 2]

print(f"\nR channel: {r}")
print(f"G channel: {g}")
print(f"B channel: {b}")

rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
print(f"\nRGB565 values: {[hex(x) for x in rgb565.flatten()]}")

high_byte = (rgb565 >> 8).astype(np.uint8)
low_byte = (rgb565 & 0xFF).astype(np.uint8)

print(f"High bytes: {[hex(x) for x in high_byte.flatten()]}")
print(f"Low bytes: {[hex(x) for x in low_byte.flatten()]}")

pixel_data = np.dstack((high_byte, low_byte)).flatten().tolist()
print(f"Final byte sequence: {[hex(x) for x in pixel_data]}")
print("\nExpected for RED, GREEN, BLUE:")
print("  [0xF8, 0x00, 0x07, 0xE0, 0x00, 0x1F]")

# Test 4: Check matplotlib output
print("\n" + "=" * 60)
print("TEST 4: Matplotlib/Cartopy Output")
print("=" * 60)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    
    # Create a simple figure with known colors
    fig, ax = plt.subplots(figsize=(3, 1), dpi=100)
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 1)
    
    # Draw red, green, blue rectangles
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((0, 0), 1, 1, facecolor='red'))
    ax.add_patch(Rectangle((1, 0), 1, 1, facecolor='green'))
    ax.add_patch(Rectangle((2, 0), 1, 1, facecolor='blue'))
    ax.axis('off')
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    
    # Save to buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', pad_inches=0)
    buf.seek(0)
    
    # Load and check
    mpl_img = Image.open(buf).convert('RGB')
    mpl_arr = np.array(mpl_img)
    
    print(f"Matplotlib image size: {mpl_img.size}")
    print(f"Matplotlib array shape: {mpl_arr.shape}")
    
    # Sample from center of each third
    h, w = mpl_arr.shape[:2]
    third = w // 3
    
    r_sample = mpl_arr[h//2, third//2]
    g_sample = mpl_arr[h//2, third + third//2]
    b_sample = mpl_arr[h//2, 2*third + third//2]
    
    print(f"\nSampled pixels from matplotlib output:")
    print(f"  Red region:   R={r_sample[0]}, G={r_sample[1]}, B={r_sample[2]}")
    print(f"  Green region: R={g_sample[0]}, G={g_sample[1]}, B={g_sample[2]}")
    print(f"  Blue region:  R={b_sample[0]}, G={b_sample[1]}, B={b_sample[2]}")
    
    plt.close(fig)
    
except Exception as e:
    print(f"Matplotlib test failed: {e}")

# Test 5: Check what the display test showed
print("\n" + "=" * 60)
print("TEST 5: Recall from test_colors.py")
print("=" * 60)
print("""
From the earlier test_colors.py, we confirmed:
- Sending 0xF800 showed RED on screen
- Sending 0x07E0 showed GREEN on screen  
- Sending 0x001F showed BLUE on screen

This means the display expects STANDARD RGB565 format.
The _test_fill() function works correctly.

The question is: why does display() show wrong colors?
""")

# Test 6: MADCTL analysis
print("=" * 60)
print("TEST 6: MADCTL Register Analysis")
print("=" * 60)
print("""
MADCTL (0x36) controls memory access and color order.
Bit layout: MY MX MV ML BGR MH X X

Current setting: 0x40 = 0100 0000
  MY  (bit 7) = 0: Top to bottom
  MX  (bit 6) = 1: Right to left (mirror X)
  MV  (bit 5) = 0: Normal
  ML  (bit 4) = 0: Normal
  BGR (bit 3) = 0: RGB order
  MH  (bit 2) = 0: Normal

Previous setting: 0x48 = 0100 1000
  BGR (bit 3) = 1: BGR order (swap R and B)

If BGR=0 and colors are wrong, the issue is in our data, not the display.
If BGR=1 and colors are wrong the other way, same conclusion.

The test_fill uses raw RGB565 values and works.
The display() function uses PIL/numpy and doesn't work.

HYPOTHESIS: The issue might be in how we're interpreting the image data,
or there's a bug in numpy array indexing.
""")

print("\n" + "=" * 60)
print("CONCLUSION")
print("=" * 60)
print("""
Run this script on the Pi to see the actual values.
If Test 3 shows correct byte sequences, the conversion is fine.
If Test 4 shows matplotlib outputting correct RGB values, that's fine too.

The remaining issue would be in:
1. How cached frames are saved/loaded (numpy npz)
2. Some other transformation we're missing
""")
