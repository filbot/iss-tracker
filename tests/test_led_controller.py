from iss_display.display.led import build_led_controller


def test_led_controller_disabled_returns_none() -> None:
    controller = build_led_controller(False, 12)
    assert controller is None
