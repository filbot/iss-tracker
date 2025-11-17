"""Render the minimum overlay needed to orient the map image."""

from __future__ import annotations

from typing import List

from PIL import Image, ImageDraw, ImageFont

from iss_display.data.iss_client import ISSFix


class FrameLayout:
    """Adds a crosshair and compact telemetry banner to the portrait image."""

    def __init__(self, width: int, height: int, *, pin_color: str) -> None:
        self.width = width
        self.height = height
        self.pin_color = pin_color
        self.font = ImageFont.load_default()

    def compose(self, base_image: Image.Image, fix: ISSFix) -> Image.Image:
        canvas = base_image.convert("RGB").resize((self.width, self.height), Image.LANCZOS)
        draw = ImageDraw.Draw(canvas)
        self._draw_crosshair(draw)
        self._draw_banner(draw, self._telemetry_lines(fix))
        return canvas

    def _draw_crosshair(self, draw: ImageDraw.ImageDraw) -> None:
        cx = self.width // 2
        cy = self.height // 2
        draw.line((cx, 0, cx, self.height), fill=self.pin_color, width=1)
        draw.line((0, cy, self.width, cy), fill=self.pin_color, width=1)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=self.pin_color, outline="white")

    def _telemetry_lines(self, fix: ISSFix) -> List[str]:
        lines = [
            f"Lat {fix.latitude:.2f}°",
            f"Lon {fix.longitude:.2f}°",
        ]
        if fix.altitude_km is not None:
            lines.append(f"Alt {fix.altitude_km:.0f} km")
        if fix.velocity_kmh is not None:
            lines.append(f"Vel {fix.velocity_kmh:.0f} km/h")
        return lines

    def _draw_banner(self, draw: ImageDraw.ImageDraw, lines: List[str]) -> None:
        if not lines:
            return

        padding = 6
        spacing = 2
        text_width = 0
        text_height = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=self.font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            text_width = max(text_width, width)
            text_height += height + spacing
        text_height -= spacing

        box_width = text_width + padding * 2
        box_height = text_height + padding * 2
        x0 = max(0, (self.width - box_width) // 2)
        y0 = padding
        x1 = x0 + box_width
        y1 = y0 + box_height

        draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 255, 230))
        text_y = y0 + padding
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=self.font)
            line_width = bbox[2] - bbox[0]
            line_height = bbox[3] - bbox[1]
            text_x = x0 + max(0, (box_width - line_width) // 2)
            draw.text((text_x, text_y), line, fill="black", font=self.font)
            text_y += line_height + spacing
