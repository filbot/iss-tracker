#!/usr/bin/env python3
"""Test script to validate display colors."""

import time
import spidev
import RPi.GPIO as GPIO

# GPIO pins - match your wiring
DC = 22
RST = 27
BL = 18

# Display dimensions
WIDTH = 320
HEIGHT = 480

# ST7796S Commands
SWRESET = 0x01
SLPOUT = 0x11
NORON = 0x13
INVON = 0x21
DISPON = 0x29
CASET = 0x2A
RASET = 0x2B
RAMWR = 0x2C
MADCTL = 0x36
COLMOD = 0x3A


def init():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(DC, GPIO.OUT)
    GPIO.setup(RST, GPIO.OUT)
    GPIO.setup(BL, GPIO.OUT)
    
    # Reset
    GPIO.output(RST, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(RST, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RST, GPIO.HIGH)
    time.sleep(0.2)
    
    global spi
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 10000000
    spi.mode = 0
    
    cmd(SWRESET)
    time.sleep(0.15)
    
    cmd(SLPOUT)
    time.sleep(0.15)
    
    cmd(COLMOD)
    data(0x55)  # 16-bit color
    
    cmd(MADCTL)
    data(0x48)  # Current setting
    
    cmd(INVON)
    cmd(NORON)
    time.sleep(0.01)
    
    cmd(DISPON)
    time.sleep(0.01)
    
    GPIO.output(BL, GPIO.HIGH)
    print("Display initialized")


def cmd(c):
    GPIO.output(DC, GPIO.LOW)
    spi.writebytes([c])


def data(d):
    GPIO.output(DC, GPIO.HIGH)
    spi.writebytes([d])


def set_window(x0, y0, x1, y1):
    cmd(CASET)
    data(x0 >> 8)
    data(x0 & 0xFF)
    data(x1 >> 8)
    data(x1 & 0xFF)
    
    cmd(RASET)
    data(y0 >> 8)
    data(y0 & 0xFF)
    data(y1 >> 8)
    data(y1 & 0xFF)
    
    cmd(RAMWR)


def fill_color(color_high, color_low, label):
    """Fill screen with a 16-bit color (high byte, low byte)."""
    print(f"Filling with {label}: 0x{color_high:02X}{color_low:02X}")
    
    set_window(0, 0, WIDTH - 1, HEIGHT - 1)
    GPIO.output(DC, GPIO.HIGH)
    
    pixel = [color_high, color_low] * 1000
    for _ in range((WIDTH * HEIGHT) // 1000):
        spi.writebytes(pixel)
    
    # Handle remainder
    remainder = (WIDTH * HEIGHT) % 1000
    if remainder:
        spi.writebytes([color_high, color_low] * remainder)


def fill_rgb(r, g, b, label):
    """Fill screen with RGB values (0-255 each)."""
    # Standard RGB565: RRRRR GGGGGG BBBBB
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    high = (rgb565 >> 8) & 0xFF
    low = rgb565 & 0xFF
    print(f"RGB({r},{g},{b}) -> RGB565: 0x{high:02X}{low:02X}")
    fill_color(high, low, label)


def test_colors():
    """Test different color encodings to find the correct one."""
    
    print("\n" + "="*50)
    print("COLOR TEST - Watch the display and note what you see")
    print("="*50)
    
    # Test 1: Raw bit patterns
    print("\n--- TEST 1: Raw 16-bit values ---")
    
    print("\nShowing 0xF800 (should be RED if RGB565)...")
    fill_color(0xF8, 0x00, "0xF800")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nShowing 0x07E0 (should be GREEN if RGB565)...")
    fill_color(0x07, 0xE0, "0x07E0")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nShowing 0x001F (should be BLUE if RGB565)...")
    fill_color(0x00, 0x1F, "0x001F")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nShowing 0xFFFF (should be WHITE)...")
    fill_color(0xFF, 0xFF, "0xFFFF")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nShowing 0x0000 (should be BLACK)...")
    fill_color(0x00, 0x00, "0x0000")
    input("Press Enter to continue... (What color do you see?) ")
    
    # Test 2: Using RGB conversion
    print("\n--- TEST 2: RGB to RGB565 conversion ---")
    
    print("\nRGB(255, 0, 0) - Pure Red...")
    fill_rgb(255, 0, 0, "Pure Red")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nRGB(0, 255, 0) - Pure Green...")
    fill_rgb(0, 255, 0, "Pure Green")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\nRGB(0, 0, 255) - Pure Blue...")
    fill_rgb(0, 0, 255, "Pure Blue")
    input("Press Enter to continue... (What color do you see?) ")
    
    print("\n" + "="*50)
    print("TEST COMPLETE")
    print("="*50)
    print("""
Based on what you saw:

If 0xF800 showed RED -> Display uses standard RGB565
If 0xF800 showed BLUE -> Display uses BGR565 (R and B swapped)
If 0xF800 showed GREEN -> Display has unusual bit ordering

Please tell me what colors you saw for each test!
""")


def cleanup():
    GPIO.cleanup()
    spi.close()


if __name__ == "__main__":
    try:
        init()
        test_colors()
    finally:
        cleanup()
