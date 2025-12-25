import pytest

from manual_channel_aligner.screenshot_utils import parse_geometry, window_bbox


def test_parse_geometry():
    assert parse_geometry("800x600+10+20") == (800, 600, 10, 20)


def test_parse_geometry_invalid():
    with pytest.raises(ValueError):
        parse_geometry("800x600")


def test_window_bbox():
    assert window_bbox(10, 20, 100, 50) == (10, 20, 110, 70)
