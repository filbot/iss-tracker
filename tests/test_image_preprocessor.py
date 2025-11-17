from pathlib import Path

from PIL import Image

from iss_display.pipeline.image_preprocessor import FrameEncoder


def test_encoder_bit_lengths(tmp_path: Path) -> None:
    width, height = 128, 250
    logical_width = 122
    encoder = FrameEncoder(
        width=width,
        height=height,
        has_red=True,
        logical_width=logical_width,
        pad_left=3,
        pad_right=3,
    )

    canvas = Image.new("RGB", (logical_width, height), "white")
    red, black = encoder.encode(canvas)

    expected_length = int(width * height / 8)
    assert len(red) == expected_length
    assert len(black) == expected_length

    canvas_red = Image.new("RGB", (logical_width, height), "red")
    red2, black2 = encoder.encode(canvas_red)
    assert red2.count(0) > 0  # red pixels should clear bits in the plane
    assert red2.count(0) < len(red2)  # padding ensures not every byte is zero
    assert black2.count(0xFF) < len(black2)
