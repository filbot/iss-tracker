
from PIL import Image
from iss_display.pipeline.layout import FrameLayout
from iss_display.data.iss_client import ISSFix

def test_layout_telemetry_lines():
    layout = FrameLayout(100, 200, pin_color="red")
    fix = ISSFix(latitude=10.0, longitude=20.0, altitude_km=400.0, velocity_kmh=27000.0, timestamp=1234567890)
    lines = layout._telemetry_lines(fix)
    assert lines == [
        ("Lat", "10.00°"),
        ("Lon", "20.00°"),
        ("Alt", "400 km"),
        ("Vel", "27000 km/h"),
    ]

def test_layout_compose():
    layout = FrameLayout(100, 200, pin_color="red")
    fix = ISSFix(latitude=10.0, longitude=20.0, altitude_km=400.0, velocity_kmh=27000.0, timestamp=1234567890)
    base_image = Image.new("RGB", (100, 200), "blue")
    result = layout.compose(base_image, fix, location_name="Pacific Ocean")
    assert result.size == (100, 200)
