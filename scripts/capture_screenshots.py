from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import tkinter as tk
from PIL import ImageGrab

from manual_channel_aligner.app import DND_AVAILABLE, ManualChannelAlignerApp, TkinterDnD
from manual_channel_aligner.screenshot_utils import parse_geometry, window_bbox


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture real app screenshots (Windows).")
    parser.add_argument(
        "images",
        nargs="+",
        help="Input images (2+ separate files or one multi-page TIFF).",
    )
    parser.add_argument("--out-dir", default="screenshots", help="Output directory.")
    parser.add_argument("--geometry", default="1280x820+40+40", help="Window geometry WxH+X+Y.")
    parser.add_argument("--delay", type=float, default=0.6, help="Seconds to wait before capture.")
    return parser.parse_args()


def _capture_window(root: tk.Tk, out_path: Path) -> None:
    root.update_idletasks()
    root.update()
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    bbox = window_bbox(x, y, width, height)
    ImageGrab.grab(bbox=bbox).save(out_path)


def main() -> int:
    args = _parse_args()
    if os.name != "nt":
        print("Screenshot capture is supported on Windows only.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height, x, y = parse_geometry(args.geometry)

    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    root.geometry(f"{width}x{height}+{x}+{y}")
    app = ManualChannelAlignerApp(root, paths=args.images)

    root.update_idletasks()
    root.update()
    time.sleep(args.delay)

    _capture_window(root, out_dir / "app-overview.png")

    if getattr(app, "sidebar_canvas", None) is not None:
        app.sidebar_canvas.yview_moveto(1.0)
        root.update_idletasks()
        root.update()
        time.sleep(max(0.2, args.delay / 2))
        _capture_window(root, out_dir / "alignment-controls.png")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
