"""Convert RGB frames into the bitplanes expected by the e-paper driver."""

from __future__ import annotations

from typing import Tuple

from PIL import Image


class FrameEncoder:
    """Transforms PIL images into red/black buffers without external deps."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        has_red: bool,
        logical_width: int,
        pad_left: int,
        pad_right: int,
    ) -> None:
        self.width = width
        self.height = height
        self.has_red = has_red
        self.logical_width = logical_width
        self.pad_left = pad_left
        self.pad_right = pad_right

        if self.pad_left + self.logical_width + self.pad_right != self.width:
            raise ValueError("Logical width plus padding must equal physical width")
        if self.width % 8 != 0:
            raise ValueError("Display width must be byte-aligned (multiple of 8)")

        self.byte_length = (self.width * self.height) // 8
        self._row_bytes = self.width // 8

    def encode(self, image: Image.Image) -> Tuple[bytes, bytes]:
        portrait = image.convert("RGB").resize((self.logical_width, self.height), Image.LANCZOS)
        canvas = Image.new("RGB", (self.width, self.height), "white")
        canvas.paste(portrait, (self.pad_left, 0))
        pixels = canvas.load()

        red_plane = bytearray([0xFF] * self.byte_length) if self.has_red else None
        black_plane = bytearray(self.byte_length)

        for y in range(self.height):
            row_offset = y * self._row_bytes
            for x in range(self.width):
                r, g, b = pixels[x, y]
                byte_index = row_offset + (x // 8)
                bit = 1 << (7 - (x % 8))

                if self.has_red and _is_redish(r, g, b):
                    red_plane[byte_index] &= ~bit
                    continue
                if _is_whitish(r, g, b):
                    black_plane[byte_index] |= bit

        red_bytes = bytes(red_plane) if self.has_red else bytes([0xFF] * self.byte_length)
        return red_bytes, bytes(black_plane)


def _is_redish(r: int, g: int, b: int) -> bool:
    target = (0xED, 0x1C, 0x24)
    dr = r - target[0]
    dg = g - target[1]
    db = b - target[2]
    distance_sq = dr * dr + dg * dg + db * db
    return distance_sq < 65 * 65


def _is_whitish(r: int, g: int, b: int) -> bool:
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance >= 190
