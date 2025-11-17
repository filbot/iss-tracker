"""Render annotations on top of downloaded map imagery."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from iss_display.data.iss_client import ISSFix


@dataclass(frozen=True)
class RegionBand:
    """Rudimentary bounding box used to describe ISS ground track."""

    label: str
    lat_range: tuple[float, float]
    lon_ranges: tuple[tuple[float, float], ...] = ((-180.0, 180.0),)

    def contains(self, latitude: float, longitude: float) -> bool:
        if not (self.lat_range[0] <= latitude <= self.lat_range[1]):
            return False
        for lon_min, lon_max in self.lon_ranges:
            if lon_min <= longitude <= lon_max:
                return True
        return False


REGION_BANDS: tuple[RegionBand, ...] = (
    RegionBand("the Arctic Circle", (70.0, 90.0)),
    RegionBand("Antarctica", (-90.0, -60.0)),
    RegionBand("North America", (15.0, 72.0), ((-170.0, -50.0),)),
    RegionBand("South America", (-60.0, 15.0), ((-90.0, -30.0),)),
    RegionBand("Europe", (35.0, 72.0), ((-25.0, 45.0),)),
    RegionBand("Africa", (-35.0, 35.0), ((-20.0, 50.0),)),
    RegionBand("the Middle East", (15.0, 40.0), ((35.0, 65.0),)),
    RegionBand("Asia", (5.0, 80.0), ((45.0, 180.0), (-180.0, -140.0))),
    RegionBand("Australia", (-50.0, -10.0), ((110.0, 180.0),)),
    RegionBand("the Indian Ocean", (-45.0, 30.0), ((20.0, 120.0),)),
    RegionBand("the Atlantic Ocean", (-60.0, 60.0), ((-70.0, 20.0),)),
    RegionBand("the Pacific Ocean", (-60.0, 60.0), ((-180.0, -70.0), (120.0, 180.0))),
    RegionBand("the Southern Ocean", (-75.0, -50.0)),
)


def _normalize_longitude(longitude: float) -> float:
    normalized = ((longitude + 180.0) % 360.0) - 180.0
    # Avoid returning -180 which can cause duplicate matching with 180
    return -180.0 if normalized == 180.0 else normalized


def describe_groundtrack(latitude: float, longitude: float) -> str:
    lon = _normalize_longitude(longitude)
    for region in REGION_BANDS:
        if region.contains(latitude, lon):
            return region.label
    return "the open ocean"


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

        telemetry_rows = self._build_telemetry_rows(fix)
        telemetry_metrics = self._measure_telemetry(
            draw,
            telemetry_rows,
            padding_x=10,
            padding_y=6,
            column_gap=8,
            line_spacing=4,
        )
        footer_text = self._build_footer_text(fix)
        footer_metrics = self._measure_footer(draw, footer_text, padding_x=10, padding_y=4)

        shared_width = max(telemetry_metrics["width"], footer_metrics["width"])
        shared_width = min(shared_width, self.width)
        x0 = max(0, (self.width - shared_width) // 2)
        x1 = x0 + shared_width

        self._draw_telemetry(
            draw,
            telemetry_rows,
            (x0, x1),
            telemetry_metrics,
            top_margin=8,
            line_spacing=4,
            padding_x=10,
            padding_y=6,
        )
        self._draw_footer(
            draw,
            footer_text,
            (x0, x1),
            footer_metrics,
            padding_x=10,
            padding_y=4,
            bottom_margin=8,
        )
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

    def _draw_telemetry(
        self,
        draw: ImageDraw.ImageDraw,
        rows: list[tuple[str, str]],
        bounds: tuple[int, int],
        metrics: dict,
        *,
        top_margin: int,
        line_spacing: int,
        padding_x: int,
        padding_y: int,
    ) -> None:
        if not rows:
            return

        x0, x1 = bounds
        y0 = top_margin
        y1 = y0 + metrics["content_height"] + padding_y * 2
        draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 200))
        text_y = y0 + padding_y
        inner_width = (x1 - x0) - padding_x * 2

        for idx, (row_metrics, (key, value)) in enumerate(zip(metrics["rows"], rows)):
            row_height = row_metrics["height"]
            key_width = row_metrics["key_width"]
            value_width = row_metrics["value_width"]
            draw.text((x0 + padding_x, text_y), key, fill="black", font=self.font)
            value_x = x0 + padding_x + inner_width - value_width
            draw.text((value_x, text_y), value, fill="black", font=self.font)
            text_y += row_height
            if idx < len(rows) - 1:
                text_y += line_spacing

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        bounds: tuple[int, int],
        metrics: dict,
        *,
        padding_x: int,
        padding_y: int,
        bottom_margin: int,
    ) -> None:
        x0, x1 = bounds
        text_width = metrics["text_width"]
        text_height = metrics["text_height"]
        total_height = text_height + padding_y * 2
        y1 = self.height - bottom_margin
        y0 = max(0, y1 - total_height)
        draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 200))
        text_x = x0 + max(0, ((x1 - x0) - text_width) // 2)
        draw.multiline_text((text_x, y0 + padding_y), text, fill="black", font=self.font, align="center")

    def _build_telemetry_rows(self, fix: ISSFix) -> list[tuple[str, str]]:
        rows = [
            ("Lat", f"{fix.latitude:.2f}"),
            ("Lon", f"{fix.longitude:.2f}"),
        ]
        if fix.altitude_km is not None:
            rows.append(("Alt", f"{fix.altitude_km:.0f} km"))
        if fix.velocity_kmh is not None:
            rows.append(("Vel", f"{fix.velocity_kmh:.0f} km/h"))
        return rows

    def _build_footer_text(self, fix: ISSFix) -> str:
        location = describe_groundtrack(fix.latitude, fix.longitude)
        return f"Somewhere over\n{location}"

    def _measure_telemetry(
        self,
        draw: ImageDraw.ImageDraw,
        rows: list[tuple[str, str]],
        *,
        padding_x: int,
        padding_y: int,
        column_gap: int,
        line_spacing: int,
    ) -> dict:
        metrics_rows: list[dict[str, int]] = []
        max_inner_width = 0
        total_height = 0
        for key, value in rows:
            key_width, key_height = self._measure_text(draw, key)
            value_width, value_height = self._measure_text(draw, value)
            row_height = max(key_height, value_height)
            metrics_rows.append(
                {
                    "height": row_height,
                    "key_width": key_width,
                    "value_width": value_width,
                }
            )
            max_inner_width = max(max_inner_width, key_width + column_gap + value_width)
            total_height += row_height

        content_height = total_height + line_spacing * max(len(rows) - 1, 0)
        total_width = max_inner_width + padding_x * 2
        return {
            "width": total_width,
            "content_height": content_height,
            "rows": metrics_rows,
        }

    def _measure_footer(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        padding_x: int,
        padding_y: int,
    ) -> dict:
        text_width, text_height = self._measure_multiline(draw, text)
        total_width = text_width + padding_x * 2
        return {
            "width": total_width,
            "text_width": text_width,
            "text_height": text_height,
        }

    def _measure_text(self, draw: ImageDraw.ImageDraw, text: str) -> tuple[int, int]:
        try:
            bbox = draw.textbbox((0, 0), text, font=self.font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            if hasattr(self.font, "getbbox"):
                bbox = self.font.getbbox(text)
                return bbox[2] - bbox[0], bbox[3] - bbox[1]
            return self.font.getsize(text)

    def _measure_multiline(self, draw: ImageDraw.ImageDraw, text: str) -> tuple[int, int]:
        try:
            bbox = draw.multiline_textbbox((0, 0), text, font=self.font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:  # Newer Pillow only exposes multiline_textbbox
            return self._approximate_multiline(text)

    def _approximate_multiline(self, text: str) -> tuple[int, int]:
        lines = text.split("\n") or [""]
        max_width = 0
        total_height = 0
        ascent = descent = 0
        if hasattr(self.font, "getmetrics"):
            ascent, descent = self.font.getmetrics()
        line_height = ascent + descent if ascent or descent else None

        for line in lines:
            if hasattr(self.font, "getbbox"):
                bbox = self.font.getbbox(line)
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
            else:
                width, height = self.font.getsize(line)
            max_width = max(max_width, width)
            total_height += line_height or height

        return max_width, total_height
