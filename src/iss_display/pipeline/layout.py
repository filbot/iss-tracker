"""Render the minimum overlay needed to orient the map image."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from iss_display.data.iss_client import ISSFix


class FrameLayout:
    """Adds a crosshair and compact telemetry banner to the portrait image."""

    def __init__(self, width: int, height: int, *, pin_color: str) -> None:
        self.width = width
        self.height = height
        self.pin_color = pin_color
        
        try:
            # Load bundled font
            font_path = Path(__file__).parent.parent / "fonts" / "Roboto-Regular.ttf"
            self.font = ImageFont.truetype(str(font_path), 16)
        except OSError:
            # Fallback to default if bundled font is missing
            self.font = ImageFont.load_default()

    def compose(self, base_image: Image.Image, fix: ISSFix, location_name: str = "Unknown") -> Image.Image:
        canvas = base_image.convert("RGB").resize((self.width, self.height), Image.LANCZOS)
        draw = ImageDraw.Draw(canvas)
        self._draw_crosshair(draw)
        self._draw_banner(draw, self._telemetry_lines(fix))
        self._draw_bottom_banner(draw, location_name)
        return canvas

    def _draw_crosshair(self, draw: ImageDraw.ImageDraw) -> None:
        cx = self.width // 2
        cy = self.height // 2
        draw.line((cx, 0, cx, self.height), fill=self.pin_color, width=1)
        draw.line((0, cy, self.width, cy), fill=self.pin_color, width=1)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=self.pin_color, outline="white")

    def _telemetry_lines(self, fix: ISSFix) -> List[Tuple[str, str]]:
        lines = [
            ("Lat", f"{fix.latitude:.2f}°"),
            ("Lon", f"{fix.longitude:.2f}°"),
        ]
        if fix.altitude_km is not None:
            lines.append(("Alt", f"{fix.altitude_km:.0f} km"))
        if fix.velocity_kmh is not None:
            lines.append(("Vel", f"{fix.velocity_kmh:.0f} km/h"))
        return lines

    def _draw_banner(self, draw: ImageDraw.ImageDraw, lines: List[Tuple[str, str]]) -> None:
        if not lines:
            return

        margin = 5
        padding = 5
        spacing = 2

        # Calculate total height
        total_text_height = 0
        for key, value in lines:
            bbox_key = draw.textbbox((0, 0), key, font=self.font)
            bbox_val = draw.textbbox((0, 0), value, font=self.font)
            h = max(bbox_key[3] - bbox_key[1], bbox_val[3] - bbox_val[1])
            total_text_height += h + spacing
        
        if total_text_height > 0:
            total_text_height -= spacing

        box_x0 = margin
        box_y0 = margin
        box_width = self.width - 2 * margin
        box_height = total_text_height + 2 * padding
        
        box_x1 = box_x0 + box_width
        box_y1 = box_y0 + box_height

        draw.rectangle((box_x0, box_y0, box_x1, box_y1), fill=(255, 255, 255, 230))
        
        text_y = box_y0 + padding
        for key, value in lines:
            # Draw key left aligned
            draw.text((box_x0 + padding, text_y), key, fill="black", font=self.font)
            
            # Draw value right aligned
            bbox_val = draw.textbbox((0, 0), value, font=self.font)
            val_width = bbox_val[2] - bbox_val[0]
            val_x = box_x1 - padding - val_width
            draw.text((val_x, text_y), value, fill="black", font=self.font)
            
            bbox_key = draw.textbbox((0, 0), key, font=self.font)
            h = max(bbox_key[3] - bbox_key[1], bbox_val[3] - bbox_val[1])
            text_y += h + spacing

    def _draw_bottom_banner(self, draw: ImageDraw.ImageDraw, location_name: str) -> None:
        margin = 5
        padding = 5
        spacing = 2

        lines = ["Somewhere over", location_name]
        
        # Calculate total height
        total_text_height = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=self.font)
            h = bbox[3] - bbox[1]
            total_text_height += h + spacing
        
        if total_text_height > 0:
            total_text_height -= spacing

        box_width = self.width - 2 * margin
        box_height = total_text_height + 2 * padding
        
        box_x0 = margin
        box_y0 = self.height - margin - box_height
        box_x1 = box_x0 + box_width
        box_y1 = box_y0 + box_height

        draw.rectangle((box_x0, box_y0, box_x1, box_y1), fill=(255, 255, 255, 230))
        
        text_y = box_y0 + padding
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=self.font)
            line_width = bbox[2] - bbox[0]
            line_height = bbox[3] - bbox[1]
            
            # Center align text
            text_x = box_x0 + (box_width - line_width) // 2
            draw.text((text_x, text_y), line, fill="black", font=self.font)
            
            text_y += line_height + spacing
