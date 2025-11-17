#!/usr/bin/env python3
"""Diagnostic tool for e-paper display hardware issues."""

import time
import sys

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not available. Run this on the Raspberry Pi.")
    sys.exit(1)


RESET_PIN = 17
DC_PIN = 25
BUSY_PIN = 24


def main():
    print("E-Paper Display Hardware Diagnostics")
    print("=" * 50)
    
    try:
        # Setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        # Configure pins
        GPIO.setup(RESET_PIN, GPIO.OUT)
        GPIO.setup(DC_PIN, GPIO.OUT)
        GPIO.setup(BUSY_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        print(f"\n1. Initial Pin States:")
        print(f"   RESET (GPIO {RESET_PIN}): Configured as OUTPUT")
        print(f"   DC    (GPIO {DC_PIN}): Configured as OUTPUT")
        print(f"   BUSY  (GPIO {BUSY_PIN}): Configured as INPUT with PULLUP")
        
        # Read BUSY pin
        busy_state = GPIO.input(BUSY_PIN)
        print(f"\n2. BUSY Pin Reading:")
        print(f"   Current state: {'HIGH (Ready)' if busy_state else 'LOW (Busy or disconnected)'}")
        
        if not busy_state:
            print("\n   ⚠️  WARNING: BUSY pin is LOW before any reset!")
            print("   This usually indicates:")
            print("     - Display is not powered")
            print("     - HAT is not properly connected")
            print("     - Display hardware failure")
            print("     - Wrong pin configuration")
        
        # Try toggling RESET
        print(f"\n3. Testing RESET Sequence:")
        GPIO.output(RESET_PIN, GPIO.HIGH)
        time.sleep(0.1)
        print(f"   RESET set HIGH, waiting 0.1s...")
        
        GPIO.output(RESET_PIN, GPIO.LOW)
        print(f"   RESET set LOW, waiting 0.2s...")
        time.sleep(0.2)
        
        GPIO.output(RESET_PIN, GPIO.HIGH)
        print(f"   RESET set HIGH, waiting 0.5s for panel to initialize...")
        time.sleep(0.5)
        
        # Check BUSY again
        busy_after_reset = GPIO.input(BUSY_PIN)
        print(f"\n4. BUSY Pin After Reset:")
        print(f"   State: {'HIGH (Good!)' if busy_after_reset else 'LOW (Problem!)'}")
        
        if not busy_after_reset:
            print("\n   ❌ BUSY pin still LOW after reset")
            print("   Hardware troubleshooting steps:")
            print("     1. Power off the Pi completely")
            print("     2. Check HAT is firmly seated on GPIO header")
            print("     3. Inspect ribbon cable connection to display")
            print("     4. Look for physical damage")
            print("     5. Try a different power supply (need 5V 2.5A minimum)")
            return 1
        else:
            print("\n   ✅ BUSY pin went HIGH - display appears functional!")
            
            # Wait and monitor
            print(f"\n5. Monitoring BUSY Pin for 10 seconds:")
            for i in range(10):
                state = GPIO.input(BUSY_PIN)
                print(f"   [{i+1}s] BUSY: {'HIGH' if state else 'LOW'}", end='\r')
                time.sleep(1)
            print()
            
            final_state = GPIO.input(BUSY_PIN)
            if final_state:
                print("\n   ✅ Display appears stable and ready")
                print("\n   Next steps:")
                print("     - Restart the service: sudo systemctl restart iss-display.service")
                print("     - Watch logs: sudo journalctl -u iss-display.service -f")
                return 0
            else:
                print("\n   ⚠️  BUSY pin went LOW during monitoring")
                print("   Display may be unstable")
                return 1
                
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 1
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        GPIO.cleanup()
        print("\nGPIO cleanup complete")


if __name__ == "__main__":
    sys.exit(main())
