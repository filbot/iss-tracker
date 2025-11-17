from PIL import Image

from iss_display.pipeline.image_preprocessor import FrameEncoder


def test_encoder_outputs_expected_lengths() -> None:
    encoder = FrameEncoder(
        width=128,
        height=250,
        has_red=True,
        logical_width=122,
        pad_left=3,
        pad_right=3,
    )

    canvas = Image.new("RGB", (122, 250), "white")
    red, black = encoder.encode(canvas)

    expected = int(128 * 250 / 8)
    assert len(red) == expected
    assert len(black) == expected

    red_canvas = Image.new("RGB", (122, 250), "red")
    red2, black2 = encoder.encode(red_canvas)
    assert red2 != red
    assert black2 != black
