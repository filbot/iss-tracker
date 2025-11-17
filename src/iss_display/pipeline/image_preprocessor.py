"""Convert RGB images into the bitplanes expected by the e-paper driver."""

from __future__ import annotations

from typing import Tuple

import numpy as np
from PIL import Image


class FrameEncoder:
    """Transforms PIL images into red/black buffers."""

    def __init__(
        self,
        width: int,
        height: int,
        *,
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

        self.byte_length = int(self.width * self.height / 8)
        self._row_bytes = self.width // 8

    def encode(self, image: Image.Image) -> Tuple[bytes, bytes]:
        portrait = image.resize((self.logical_width, self.height)).convert("RGB")
        canvas = Image.new("RGB", (self.width, self.height), "white")
        canvas.paste(portrait, (self.pad_left, 0))

        data = np.asarray(canvas)
        red_mask = self._is_red(data) if self.has_red else np.zeros((self.height, self.width), dtype=bool)
        white_mask = self._is_white(data, red_mask)

        if self.has_red:
            # Waveshare buffers encode white pixels as 1s; flip the mask so red pixels clear bits.
            red_buffer = self._pack_mask(np.logical_not(red_mask))
        else:
            red_buffer = bytes([0xFF]) * self.byte_length
        # Datasheet expects white pixels to be set to 1 in the black channel.
        black_buffer = self._pack_mask(white_mask)

        return red_buffer, black_buffer

    def _is_red(self, data: np.ndarray) -> np.ndarray:
        target = np.array([0xED, 0x1C, 0x24])
        diff = data.astype(np.int16) - target
        dist = np.sqrt((diff**2).sum(axis=2))
        return dist < 65

    def _is_white(self, data: np.ndarray, red_mask: np.ndarray) -> np.ndarray:
        gray = (0.299 * data[:, :, 0] + 0.587 * data[:, :, 1] + 0.114 * data[:, :, 2]).astype(np.uint8)
        white_mask = (gray >= 190) & (~red_mask)
        return white_mask

    def _pack_mask(self, mask: np.ndarray) -> bytes:
        """Pack boolean mask into bytes, matching waveshare getbuffer() format.
        
        Standard format: left-to-right, top-to-bottom, MSB first within each byte.
        Each byte contains 8 pixels, with the leftmost pixel in the MSB.
        """
        buf = bytearray(self.byte_length)
        row_bytes = self._row_bytes
        for y in range(self.height):
            row = mask[y]
            base = row_bytes * y
            for x in range(self.width):
                if not row[x]:
                    continue
                byte_index = base + (x // 8)
                bit_position = 7 - (x % 8)  # MSB first
                buf[byte_index] |= 1 << bit_position
        return bytes(buf)
