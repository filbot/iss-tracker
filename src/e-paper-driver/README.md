# Waveshare 2.13" V4 Tri-color Demo

This project targets the black/white/red variant of Waveshare's 2.13" V4 panel. It includes the updated driver (`epd2in13b_V4.py`), the hardware abstraction layer (`epdconfig.py`), tri-color image helpers (`tri_color_image.py`), and an ergonomic demo script capable of rendering arbitrary bitmaps or a built-in sample scene.

## 1. Prepare the Raspberry Pi
1. Update the OS and install the SPI/Python build dependencies:

```sh
sudo apt update
sudo apt install -y python3-pip python3-pil python3-spidev python3-gpiozero python3-rpi.gpio
```

2. Enable SPI, then reboot:

```sh
sudo raspi-config nonint do_spi 0
sudo reboot
```

After reboot, confirm `/dev/spidev0.0` exists. If you prefer the graphical raspi-config wizard, use **Interface Options → SPI** instead of the non-interactive command.

## 2. Wire the display (BCM numbers)

| Display pin | Raspberry Pi pin |
| ----------- | ---------------- |
| VCC         | 3.3V or 5V       |
| GND         | GND              |
| DIN         | MOSI (BCM10)     |
| CLK         | SCLK (BCM11)     |
| CS          | CE0 (BCM8)       |
| DC          | BCM25            |
| RST         | BCM17            |
| BUSY        | BCM24            |
| PWR (if present) | BCM18       |

`epdconfig.py` drives those BCM pins via `gpiozero`, so double-check jumpers before powering on.

## 3. Clone and install Python requirements

```sh
git clone <your repo url> e-paper-driver
cd e-paper-driver
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

(`gpiozero` and `spidev` also come from the `python3-*` Debian packages above, but keeping them in `requirements.txt` helps when redeploying into a virtual environment.)

## 4. Run the demo

```sh
python3 demo.py                 # render the built-in sample artwork
python3 demo.py pic/logo.bmp    # render an asset from the pic/ folder
python3 demo.py /tmp/frame.png  # render an arbitrary file on disk
```

`demo.py` now accepts optional CLI flags:

| Flag | Description |
|------|-------------|
| `bitmap` | Optional positional argument pointing to a bitmap/PNG/JPEG. Relative names are resolved against the `pic/` directory for backwards compatibility. |
| `--hold <seconds>` | How long to keep the rendered frame visible before sleeping (default: 15). |
| `--no-clear` | Skip calling `epd.Clear()` before displaying the new frame. Useful for quick successive updates. |

Under the hood, the script feeds your image through `tri_color_image.prepare_frame`, which resizes it for the panel, rotates it 180° (the display is mounted upside down in the enclosure), and converts it into the two bitplanes (`black` and `red`) that the tri-color EPD expects. Any pixels classified as "red" (bright red with low green/blue components) go to the red overlay; dark pixels fall back to black; everything else remains white.

If you ever remount the panel in a different orientation, adjust the `MOUNT_ROTATION_DEGREES` constant in `tri_color_image.py` instead of editing every caller.

Run the command from the repository root so `demo.py`, `epd2in13_V4.py`, and `epdconfig.py` stay on the same import path; the driver uses a plain `import epdconfig`, so moving files into different folders will trigger the "attempted relative import" error observed earlier.

## 5. Troubleshooting
- `ImportError: No module named spidev/gpiozero` → ensure SPI is enabled and rerun the install step (the modules are only available on Raspberry Pi OS).
- `RuntimeError: Display init failed` → usually indicates BUSY never drops low. Re-check wiring, especially BUSY/RST, and confirm the ribbon cable is seated.
- Garbled pixels → the panel prefers full refreshes after large content changes. If ghosting remains, call `epd.Clear()` between frames.
- Red pixels missing → confirm your source artwork uses a vivid red (high R, low G/B). Fine-tune the thresholds passed to `tri_color_image.prepare_frame` if you need a different color mapping.
- Hanging on `e-Paper busy` → the display did not acknowledge the update, often due to insufficient 3.3V supply. Use a shorter jumper harness or feed 5V to the VCC pin (the board regulates it down).
