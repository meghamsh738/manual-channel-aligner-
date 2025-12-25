from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from PIL import Image, ImageOps, TiffImagePlugin


@dataclass
class TransformState:
    dx: float = 0.0
    dy: float = 0.0
    angle_deg: float = 0.0


@dataclass
class ChannelStack:
    channels: List[Image.Image]
    source_paths: List[str]
    tiffinfo: Optional[TiffImagePlugin.ImageFileDirectory_v2] = None
    save_kwargs: Optional[dict] = None


def apply_transform(image: Image.Image, state: TransformState, resample: int) -> Image.Image:
    out = image.copy()
    if state.angle_deg:
        out = out.rotate(state.angle_deg, resample=resample, expand=False, fillcolor=0)
    if state.dx or state.dy:
        out = out.transform(
            out.size,
            Image.AFFINE,
            (1, 0, -state.dx, 0, 1, -state.dy),
            resample=resample,
            fillcolor=0,
        )
    return out


def to_display_gray(
    image: Image.Image,
    display_range: Optional[tuple[float, float]] = None,
) -> Image.Image:
    if display_range is not None:
        min_val, max_val = display_range
        return _apply_display_levels(image, min_val, max_val)
    if image.mode != "L":
        image = image.convert("L")
    return ImageOps.autocontrast(image)


def tint_channel(
    gray_image: Image.Image,
    color: tuple[int, int, int],
    display_range: Optional[tuple[float, float]] = None,
) -> Image.Image:
    gray = to_display_gray(gray_image, display_range=display_range)
    return ImageOps.colorize(gray, black=(0, 0, 0), white=color)


def compose_overlay(
    reference: Image.Image,
    moving: Image.Image,
    opacity: float,
    moving_color: tuple[int, int, int],
    alpha_mode: str = "intensity",
    display_range: Optional[tuple[float, float]] = None,
) -> Image.Image:
    base_gray = to_display_gray(reference, display_range=display_range)
    base = Image.merge("RGB", (base_gray, base_gray, base_gray))

    overlay_gray = to_display_gray(moving, display_range=display_range)
    overlay_rgb = ImageOps.colorize(overlay_gray, black=(0, 0, 0), white=moving_color)
    alpha_value = int(max(0.0, min(opacity, 1.0)) * 255)
    if alpha_mode == "constant":
        alpha = Image.new("L", overlay_gray.size, alpha_value)
    else:
        alpha = overlay_gray.point(lambda v: int(v * max(0.0, min(opacity, 1.0))))
    overlay = overlay_rgb.copy()
    overlay.putalpha(alpha)

    composed = base.convert("RGBA")
    composed.alpha_composite(overlay)
    return composed.convert("RGB")


def load_channels_from_paths(paths: Sequence[str]) -> ChannelStack:
    if not paths:
        raise ValueError("No input paths provided.")

    source_paths = list(paths)
    tiffinfo: Optional[TiffImagePlugin.ImageFileDirectory_v2] = None
    save_kwargs: dict = {}

    if len(paths) > 1:
        channels = []
        for idx, path in enumerate(paths):
            image = Image.open(path)
            if idx == 0:
                tiffinfo = _extract_tiffinfo(image)
                save_kwargs = _extract_save_kwargs(image)
            channels.append(image.copy())
            image.close()
        _ensure_same_size(channels)
        return ChannelStack(channels=channels, source_paths=source_paths, tiffinfo=tiffinfo, save_kwargs=save_kwargs)

    path = paths[0]
    image = Image.open(path)

    channels: List[Image.Image] = []
    n_frames = getattr(image, "n_frames", 1)
    if n_frames > 1:
        tiffinfo = _extract_tiffinfo(image)
        save_kwargs = _extract_save_kwargs(image)
        for idx in range(n_frames):
            image.seek(idx)
            channels.append(image.copy())
        _ensure_same_size(channels)
        image.close()
        return ChannelStack(channels=channels, source_paths=source_paths, tiffinfo=tiffinfo, save_kwargs=save_kwargs)

    bands = image.getbands()
    if len(bands) > 1:
        channels = [band.copy() for band in image.split()]
        _ensure_same_size(channels)
        tiffinfo = _extract_tiffinfo(image)
        save_kwargs = _extract_save_kwargs(image)
        image.close()
        return ChannelStack(channels=channels, source_paths=source_paths, tiffinfo=tiffinfo, save_kwargs=save_kwargs)

    tiffinfo = _extract_tiffinfo(image)
    save_kwargs = _extract_save_kwargs(image)
    channel = image.copy()
    image.close()
    return ChannelStack(channels=[channel], source_paths=source_paths, tiffinfo=tiffinfo, save_kwargs=save_kwargs)


def save_channels(
    channels: Sequence[Image.Image],
    path: str,
    tiffinfo: Optional[TiffImagePlugin.ImageFileDirectory_v2] = None,
    save_kwargs: Optional[dict] = None,
) -> None:
    if not channels:
        raise ValueError("No channels to save.")

    first = channels[0]
    rest = list(channels[1:])
    kwargs = dict(save_kwargs or {})
    if tiffinfo is not None:
        kwargs["tiffinfo"] = _copy_tiffinfo(tiffinfo)
    first.save(path, save_all=True, append_images=rest, **kwargs)


def add_alignment_tag(
    tiffinfo: Optional[TiffImagePlugin.ImageFileDirectory_v2],
    tag_text: str = "Manual Aligned",
) -> TiffImagePlugin.ImageFileDirectory_v2:
    info = _copy_tiffinfo(tiffinfo) if tiffinfo is not None else TiffImagePlugin.ImageFileDirectory_v2()
    existing = info.get(270)
    if existing:
        existing_text = str(existing)
        if tag_text.lower() not in existing_text.lower():
            info[270] = f"{existing_text} | {tag_text}"
    else:
        info[270] = tag_text
    return info


def _ensure_same_size(channels: Iterable[Image.Image]) -> None:
    sizes = {im.size for im in channels}
    if len(sizes) != 1:
        raise ValueError("All channels must have the same dimensions.")


def _extract_tiffinfo(image: Image.Image) -> Optional[TiffImagePlugin.ImageFileDirectory_v2]:
    tag_v2 = getattr(image, "tag_v2", None)
    if not tag_v2:
        return None
    info = TiffImagePlugin.ImageFileDirectory_v2()
    for tag, value in tag_v2.items():
        info[tag] = value
    return info


def _copy_tiffinfo(
    tiffinfo: TiffImagePlugin.ImageFileDirectory_v2,
) -> TiffImagePlugin.ImageFileDirectory_v2:
    info = TiffImagePlugin.ImageFileDirectory_v2()
    for tag, value in tiffinfo.items():
        info[tag] = value
    return info


def _extract_save_kwargs(image: Image.Image) -> dict:
    info = getattr(image, "info", {}) or {}
    save_kwargs: dict = {}
    dpi = info.get("dpi")
    if dpi:
        save_kwargs["dpi"] = dpi
    compression = info.get("compression")
    if compression:
        save_kwargs["compression"] = compression
    icc_profile = info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    return save_kwargs


def _apply_display_levels(image: Image.Image, min_val: float, max_val: float) -> Image.Image:
    if max_val <= min_val:
        return image.convert("L")
    if image.mode not in ("L", "I", "I;16", "F"):
        image = image.convert("L")
    scale = 255.0 / (max_val - min_val)

    def mapper(v: float) -> int:
        if v <= min_val:
            return 0
        if v >= max_val:
            return 255
        return int((v - min_val) * scale)

    return image.point(mapper, mode="L")
