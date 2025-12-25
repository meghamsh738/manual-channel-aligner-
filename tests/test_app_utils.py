from PIL import Image

from manual_channel_aligner.app import (
    affine_matrix_for_crop,
    clamp_scroll_fraction,
    compute_fit_scale,
    parse_drop_files,
)
from manual_channel_aligner.core import TransformState, apply_transform


def test_parse_drop_files_braced():
    data = "{/tmp/my file.tif} /tmp/other.tif"
    paths = parse_drop_files(data)
    assert paths[0] == "/tmp/my file.tif"
    assert paths[1] == "/tmp/other.tif"


def test_parse_drop_files_empty():
    assert parse_drop_files("") == []


def test_compute_fit_scale():
    assert compute_fit_scale((100, 100), (200, 200)) == 1.0
    assert compute_fit_scale((400, 200), (200, 200)) == 0.5


def test_clamp_scroll_fraction():
    assert clamp_scroll_fraction(0.2, scroll_w=100, canvas_w=100) == 0.0
    assert clamp_scroll_fraction(-0.5, scroll_w=200, canvas_w=100) == 0.0
    assert clamp_scroll_fraction(0.8, scroll_w=200, canvas_w=100) == 0.5


def test_affine_crop_matches_full_transform():
    img = Image.new("L", (20, 20))
    for y in range(20):
        for x in range(20):
            img.putpixel((x, y), (x + y * 20) % 256)

    state = TransformState(dx=2.0, dy=-3.0, angle_deg=15.0)
    full = apply_transform(img, state, Image.NEAREST)

    out_x0, out_y0 = 5, 4
    out_w, out_h = 8, 7
    matrix = affine_matrix_for_crop(state, img.size, out_x0, out_y0)
    crop = img.transform((out_w, out_h), Image.AFFINE, matrix, resample=Image.NEAREST, fillcolor=0)
    expected = full.crop((out_x0, out_y0, out_x0 + out_w, out_y0 + out_h))

    assert list(crop.getdata()) == list(expected.getdata())
