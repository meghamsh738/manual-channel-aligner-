from PIL import Image

from PIL import TiffImagePlugin

from manual_channel_aligner.core import (
    TransformState,
    add_alignment_tag,
    apply_transform,
    compose_overlay,
    load_channels_from_paths,
    save_channels,
)


def test_apply_transform_translation():
    img = Image.new("L", (5, 5), 0)
    img.putpixel((2, 2), 255)

    state = TransformState(dx=1, dy=-1, angle_deg=0)
    out = apply_transform(img, state, Image.NEAREST)

    assert out.getpixel((3, 1)) == 255
    assert out.getpixel((2, 2)) == 0


def test_apply_transform_noop():
    img = Image.new("L", (4, 4), 0)
    img.putpixel((1, 1), 200)

    out = apply_transform(img, TransformState(), Image.NEAREST)
    assert list(out.getdata()) == list(img.getdata())


def test_load_channels_from_paths_rgb(tmp_path):
    img = Image.new("RGB", (3, 3), (10, 20, 30))
    path = tmp_path / "rgb.png"
    img.save(path)

    stack = load_channels_from_paths([str(path)])
    assert len(stack.channels) == 3
    assert stack.channels[0].mode == "L"


def test_load_channels_from_paths_la(tmp_path):
    img = Image.new("LA", (3, 3), (10, 200))
    path = tmp_path / "la.tif"
    img.save(path)

    stack = load_channels_from_paths([str(path)])
    assert len(stack.channels) == 2


def test_compose_overlay_output():
    ref = Image.new("L", (4, 4), 100)
    mov = Image.new("L", (4, 4), 200)

    out = compose_overlay(ref, mov, 0.5, (255, 0, 0))
    assert out.mode == "RGB"
    assert out.size == (4, 4)


def test_compose_overlay_constant_alpha_with_levels():
    ref = Image.new("L", (2, 2), 0)
    mov = Image.new("L", (2, 2), 10)

    out = compose_overlay(ref, mov, 1.0, (255, 0, 0), alpha_mode="constant", display_range=(0, 10))
    r, g, b = out.getpixel((0, 0))
    assert r > 0
    assert r >= g
    assert r >= b


def test_save_channels_multi_page(tmp_path):
    ch1 = Image.new("L", (2, 2), 0)
    ch2 = Image.new("L", (2, 2), 255)
    out_path = tmp_path / "stack.tif"

    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[270] = "original"
    save_channels([ch1, ch2], str(out_path), tiffinfo=add_alignment_tag(info))
    loaded = Image.open(out_path)
    assert getattr(loaded, "n_frames", 1) == 2
    assert "Manual Aligned" in str(loaded.tag_v2.get(270))


def test_add_alignment_tag_no_existing():
    info = TiffImagePlugin.ImageFileDirectory_v2()
    updated = add_alignment_tag(info)
    assert updated.get(270) == "Manual Aligned"
