from iss_display.data.geography import get_common_area_name


def test_geography_lookup():
    # Test Continents
    assert get_common_area_name(48.85, 2.35) == "Europe"        # Paris
    assert get_common_area_name(35.68, 139.76) == "Asia"        # Tokyo
    assert get_common_area_name(40.71, -74.00) == "N. America"  # NYC
    assert get_common_area_name(-33.86, 151.20) == "Australia"  # Sydney
    assert get_common_area_name(-22.90, -43.17) == "S. America" # Rio
    assert get_common_area_name(-1.29, 36.82) == "Africa"       # Nairobi

    # Test Oceans
    assert get_common_area_name(0, -150) == "Pacific"
    assert get_common_area_name(0, -30) == "Atlantic"
    assert get_common_area_name(0, 75) == "Indian"
    assert get_common_area_name(80, 0) == "Arctic"
    assert get_common_area_name(-70, 0) == "Southern"
