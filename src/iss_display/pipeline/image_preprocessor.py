"""Convert RGB frames into the bitplanes expected by the e-paper driver."""

from __future__ import annotations

from typing import Tuple

from PIL import Image

_RED_DIFF_MIN = 30


class FrameEncoder:
    """Transforms PIL images into tri-color bitplanes without vendor imports."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        has_red: bool,
        logical_width: int,
        pad_left: int,
        pad_right: int,
        rotate_degrees: int = 180,
        black_threshold: int = 96,
        red_green_max: int = 80,
        red_blue_max: int = 80,
        red_min: int = 150,
    ) -> None:
        self.width = width
        self.height = height
        self.has_red = has_red
        self.logical_width = logical_width
        self.pad_left = pad_left
        self.pad_right = pad_right
        self.rotate_degrees = rotate_degrees % 360
        self.black_threshold = black_threshold
        self.red_green_max = red_green_max
        self.red_blue_max = red_blue_max
        self.red_min = red_min

        if self.rotate_degrees % 90 != 0:
            raise ValueError("Display rotation must be expressed in 90° increments")
        if self.pad_left + self.logical_width + self.pad_right != self.width:
            raise ValueError("Logical width plus padding must equal physical width")

        self._row_bytes = (self.width + 7) // 8
        self.byte_length = self._row_bytes * self.height

    def encode(self, image: Image.Image) -> Tuple[bytes, bytes]:
        portrait = image.convert("RGB").resize((self.logical_width, self.height), Image.LANCZOS)
        canvas = Image.new("RGB", (self.width, self.height), "white")
        canvas.paste(portrait, (self.pad_left, 0))

        if self.rotate_degrees:
            canvas = canvas.rotate(self.rotate_degrees, expand=True)
            if canvas.size != (self.width, self.height):
                canvas = canvas.resize((self.width, self.height), Image.LANCZOS)

        pixels = canvas.load()
        red_plane = bytearray([0xFF] * self.byte_length)
        black_plane = bytearray([0xFF] * self.byte_length)

        for y in range(self.height):
            row_offset = y * self._row_bytes
            for x in range(self.width):
                r, g, b = pixels[x, y]
                byte_index = row_offset + (x // 8)
                bit = 0x80 >> (x % 8)

                is_red_pixel = _is_red(r, g, b, self.red_min, self.red_green_max, self.red_blue_max)
                if is_red_pixel and self.has_red:
                    red_plane[byte_index] &= ~bit
                    continue

                if is_red_pixel and not self.has_red:
                    is_black_pixel = True
                else:
                    is_black_pixel = _is_black(r, g, b, self.black_threshold)

                if is_black_pixel:
                    black_plane[byte_index] &= ~bit

        red_bytes = bytes(red_plane)
        if not self.has_red:
            red_bytes = bytes([0xFF] * self.byte_length)
        return red_bytes, bytes(black_plane)


def _is_black(r: int, g: int, b: int, threshold: int) -> bool:
    return max(r, g, b) <= threshold


def _is_red(r: int, g: int, b: int, red_min: int, green_max: int, blue_max: int) -> bool:
    return r >= red_min and g <= green_max and b <= blue_max and (r - max(g, b)) >= _RED_DIFF_MIN
