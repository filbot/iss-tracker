"""Render annotations on top of downloaded map imagery."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from iss_display.data.iss_client import ISSFix


class FrameLayout:
    """Adds overlays (pin + telemetry text) on top of the portrait map."""

    def __init__(self, width: int, height: int, *, pin_color: str) -> None:
        self.width = width
        self.height = height
        self.pin_color = pin_color
        self.font = ImageFont.load_default()

    def compose(self, base_image: Image.Image, fix: ISSFix) -> Image.Image:
        background = base_image.convert("RGB").copy()
        if background.size != (self.width, self.height):
            background = background.resize((self.width, self.height), Image.LANCZOS)

        draw = ImageDraw.Draw(background)
        self._draw_pin(draw)
        self._draw_telemetry(draw, fix)
        return background

    def _draw_pin(self, draw: ImageDraw.ImageDraw) -> None:
        center = (self.width // 2, self.height // 2)
        radius = 5
        draw.ellipse(
            [
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ],
            outline=self.pin_color,
            width=2,
            fill=self.pin_color,
        )

    def _draw_telemetry(self, draw: ImageDraw.ImageDraw, fix: ISSFix) -> None:
        text_lines = [
            f"Lat: {fix.latitude:.2f}",
            f"Lon: {fix.longitude:.2f}",
        ]
        if fix.altitude_km is not None:
            text_lines.append(f"Alt: {fix.altitude_km:.0f} km")
        if fix.velocity_kmh is not None:
            text_lines.append(f"Vel: {fix.velocity_kmh:.0f} km/h")

        text = "\n".join(text_lines)
        padding = 6
        text_width, text_height = self._measure_multiline(draw, text)
        box = [
            padding,
            padding,
            padding + text_width + 4,
            padding + text_height + 4,
        ]
        draw.rectangle(box, fill=(255, 255, 255, 200))
        draw.multiline_text((padding + 2, padding + 2), text, fill="black", font=self.font)

    def _measure_multiline(self, draw: ImageDraw.ImageDraw, text: str) -> tuple[int, int]:
        try:
            bbox = draw.multiline_textbbox((0, 0), text, font=self.font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:  # Pillow < 8.0 fallback
            return draw.multiline_textsize(text, font=self.font)
