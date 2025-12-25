from __future__ import annotations

import argparse
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from urllib.parse import unquote, urlparse

from PIL import Image, ImageEnhance, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    DND_AVAILABLE = False
    DND_FILES = None
    TkinterDnD = None


def parse_drop_files(data: str) -> List[str]:
    if not data:
        return []
    tokens = re.findall(r"{[^}]+}|[^\\s]+", data)
    paths = [normalize_drop_path(token.strip().strip("{}")) for token in tokens]
    return [p for p in paths if p]


def compute_fit_scale(image_size: tuple[int, int], canvas_size: tuple[int, int]) -> float:
    img_w, img_h = image_size
    canvas_w, canvas_h = canvas_size
    if img_w <= 0 or img_h <= 0:
        return 1.0
    if canvas_w <= 0 or canvas_h <= 0:
        return 1.0
    scale = min(canvas_w / img_w, canvas_h / img_h, 1.0)
    return max(scale, 0.05)


def clamp_scroll_fraction(value: float, scroll_w: float, canvas_w: float) -> float:
    if scroll_w <= 0:
        return 0.0
    max_start = max(0.0, 1.0 - (canvas_w / scroll_w))
    return min(max(value, 0.0), max_start)


def affine_matrix_for_state(state: "TransformState", size: tuple[int, int]) -> tuple[float, float, float, float, float, float]:
    angle = math.radians(state.angle_deg)
    ca = math.cos(angle)
    sa = math.sin(angle)
    cx = size[0] / 2.0
    cy = size[1] / 2.0
    dx = state.dx
    dy = state.dy
    a0 = ca
    a1 = -sa
    a2 = (-ca * dx) - (ca * cx) + (sa * dy) + (sa * cy) + cx
    b0 = sa
    b1 = ca
    b2 = (-sa * dx) - (sa * cx) - (ca * dy) - (ca * cy) + cy
    return (a0, a1, a2, b0, b1, b2)


def affine_matrix_for_crop(
    state: "TransformState",
    size: tuple[int, int],
    out_x0: int,
    out_y0: int,
) -> tuple[float, float, float, float, float, float]:
    a0, a1, a2, b0, b1, b2 = affine_matrix_for_state(state, size)
    a2 = a2 + a0 * out_x0 + a1 * out_y0
    b2 = b2 + b0 * out_x0 + b1 * out_y0
    return (a0, a1, a2, b0, b1, b2)


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _: tk.Event) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + 18
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(self.tip, text=self.text, style="Tooltip.TLabel", justify="left")
        label.pack(ipadx=6, ipady=4)

    def _hide(self, _: tk.Event) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


def normalize_drop_path(value: str) -> str:
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if os.name == "nt" and value.startswith("/"):
            value = value[1:]
    return value

from .core import (
    ChannelStack,
    TransformState,
    add_alignment_tag,
    apply_transform,
    load_channels_from_paths,
    save_channels,
    to_display_gray,
)


@dataclass
class UiTokens:
    pad_sm: int = 6
    pad_md: int = 12
    pad_lg: int = 18
    sidebar_width: int = 320
    canvas_min_w: int = 520
    canvas_min_h: int = 360
    preview_max_dim: int = 1600
    preview_max_pixels: int = 2_000_000
    zoom_min: float = 0.1
    zoom_max: float = 32.0
    zoom_step: float = 0.25


@dataclass
class UiColors:
    bg: str = "#F4F1EC"
    panel: str = "#FBF9F5"
    text: str = "#1E1914"
    muted: str = "#6F665F"
    accent: str = "#C06A33"
    accent_dark: str = "#A15426"
    border: str = "#D7CFC6"
    canvas_bg: str = "#14110D"
    canvas_border: str = "#D1C9BF"
    highlight: str = "#F2E6D8"


class ManualChannelAlignerApp(ttk.Frame):
    SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}

    def __init__(self, master: tk.Tk, paths: Optional[List[str]] = None) -> None:
        super().__init__(master)
        self.master = master
        self.tokens = UiTokens()
        self.colors = UiColors()

        self.channels: List[Image.Image] = []
        self.preview_channels: List[Image.Image] = []
        self.preview_scale: float = 1.0
        self.display_channels: List[Image.Image] = []
        self.reference_rgb_cache: List[Image.Image] = []
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.zoom_label_var = tk.StringVar(value="Zoom: 100%")
        self._pan_anchor: Optional[tuple[int, int]] = None
        self._needs_center_view = False
        self._scroll_w = 0
        self._scroll_h = 0
        self._canvas_w = 0
        self._canvas_h = 0
        self._render_job: Optional[str] = None
        self._overlay_cache_key: Optional[tuple] = None
        self._overlay_cache: Optional[Image.Image] = None
        self._display_cache_version = 0
        self.fast_preview_var = tk.BooleanVar(value=False)
        self.preview_quality_var = tk.DoubleVar(value=2.0)
        self.preview_quality_label_var = tk.StringVar(value="Preview: 2.0 MP")
        self._preview_quality_backup: Optional[float] = None
        self.use_gpu_var = tk.BooleanVar(value=False)
        self.full_res_view_var = tk.BooleanVar(value=False)
        self.gpu_available = False
        self.gpu_status = "GPU: unavailable"
        self._full_auto_ranges: Optional[List[Optional[tuple[float, float]]]] = None
        self.transforms: List[TransformState] = []
        self.reference_index = 0
        self.active_index = 0
        self.last_save_path: Optional[str] = None
        self.source_paths: List[str] = []
        self.tiffinfo = None
        self.save_kwargs: Optional[dict] = None
        self.dnd_enabled = False

        self.reference_combo: Optional[ttk.Combobox] = None
        self.active_combo: Optional[ttk.Combobox] = None
        self.display_min_entry: Optional[ttk.Entry] = None
        self.display_max_entry: Optional[ttk.Entry] = None
        self.display_min_scale: Optional[ttk.Scale] = None
        self.display_max_scale: Optional[ttk.Scale] = None
        self.brightness_scale: Optional[ttk.Scale] = None
        self.preview_quality_scale: Optional[ttk.Scale] = None
        self.h_scrollbar: Optional[ttk.Scrollbar] = None
        self.v_scrollbar: Optional[ttk.Scrollbar] = None
        self.sidebar_canvas: Optional[tk.Canvas] = None
        self.sidebar_scrollbar: Optional[ttk.Scrollbar] = None
        self.sidebar_inner: Optional[ttk.Frame] = None
        self._sidebar_window: Optional[int] = None

        self.resample_label_to_method = {
            "Nearest": Image.NEAREST,
            "Bilinear": Image.BILINEAR,
            "Bicubic": Image.BICUBIC,
        }

        self._build_ui()
        self._bind_keys()
        self._configure_drag_drop()

        if paths:
            self.load_images(paths)
        else:
            self._render_empty_state()

    def _build_ui(self) -> None:
        self.master.title("Manual Channel Aligner App")
        self.master.minsize(1024, 700)
        self.master.configure(background=self.colors.bg)

        style = ttk.Style(self.master)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self._setup_fonts()

        style.configure("Aligner.TFrame", background=self.colors.bg)
        style.configure("Panel.TFrame", background=self.colors.panel)
        style.configure("Aligner.TLabel", background=self.colors.bg, foreground=self.colors.text, font=self.fonts["body"])
        style.configure("Panel.TLabel", background=self.colors.panel, foreground=self.colors.text, font=self.fonts["body"])
        style.configure(
            "PanelMuted.TLabel",
            background=self.colors.panel,
            foreground=self.colors.muted,
            font=self.fonts["caption"],
        )
        style.configure(
            "Muted.TLabel",
            background=self.colors.bg,
            foreground=self.colors.muted,
            font=self.fonts["caption"],
        )
        style.configure(
            "Aligner.TLabelframe",
            background=self.colors.panel,
            foreground=self.colors.text,
            font=self.fonts["section"],
        )
        style.configure(
            "Aligner.TLabelframe.Label",
            background=self.colors.panel,
            foreground=self.colors.text,
            font=self.fonts["section"],
        )
        style.configure(
            "Aligner.TEntry",
            fieldbackground="#FFFFFF",
            background=self.colors.panel,
            foreground=self.colors.text,
            padding=6,
        )
        style.configure(
            "Aligner.TCombobox",
            fieldbackground="#FFFFFF",
            background=self.colors.panel,
            foreground=self.colors.text,
            padding=6,
        )
        style.configure(
            "Primary.TButton",
            background=self.colors.accent,
            foreground="#FFFFFF",
            padding=(12, 6),
            font=self.fonts["button"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", self.colors.accent_dark)],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "Secondary.TButton",
            background=self.colors.panel,
            foreground=self.colors.text,
            padding=(10, 6),
            font=self.fonts["button"],
        )
        style.configure(
            "Panel.TCheckbutton",
            background=self.colors.panel,
            foreground=self.colors.text,
            font=self.fonts["body"],
        )
        style.configure(
            "Tooltip.TLabel",
            background="#1C1916",
            foreground="#F7F2EB",
            font=self.fonts["caption"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Help.TLabel",
            background=self.colors.panel,
            foreground=self.colors.muted,
            font=self.fonts["caption"],
        )
        self._init_gpu()

        self.pack(fill="both", expand=True)
        self.configure(style="Aligner.TFrame")

        header = ttk.Frame(self, style="Aligner.TFrame")
        header.pack(fill="x", padx=self.tokens.pad_lg, pady=(self.tokens.pad_lg, self.tokens.pad_md))

        title = ttk.Label(header, text="Manual Channel Aligner App", style="Aligner.TLabel")
        title.configure(font=self.fonts["title"])
        title.pack(side="left")

        button_bar = ttk.Frame(header, style="Aligner.TFrame")
        button_bar.pack(side="right")

        ttk.Button(button_bar, text="Open Images", command=self._open_images_dialog, style="Secondary.TButton").pack(
            side="left", padx=(0, self.tokens.pad_sm)
        )
        ttk.Button(button_bar, text="Save Aligned", command=self._save_aligned, style="Primary.TButton").pack(
            side="left", padx=(0, self.tokens.pad_sm)
        )
        ttk.Button(button_bar, text="Reset All", command=self._reset_all, style="Secondary.TButton").pack(side="left")

        body = ttk.Frame(self, style="Aligner.TFrame")
        body.pack(fill="both", expand=True, padx=self.tokens.pad_lg, pady=(0, self.tokens.pad_lg))

        canvas_container = ttk.Frame(body, style="Aligner.TFrame")
        canvas_container.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_container,
            width=self.tokens.canvas_min_w,
            height=self.tokens.canvas_min_h,
            background=self.colors.canvas_bg,
            highlightthickness=2,
            highlightbackground=self.colors.canvas_border,
            takefocus=1,
        )
        self.h_scrollbar = ttk.Scrollbar(
            canvas_container, orient="horizontal", command=lambda *args: self._on_scroll("x", *args)
        )
        self.v_scrollbar = ttk.Scrollbar(
            canvas_container, orient="vertical", command=lambda *args: self._on_scroll("y", *args)
        )
        self.canvas.configure(xscrollcommand=self.h_scrollbar.set, yscrollcommand=self.v_scrollbar.set)

        canvas_container.grid_rowconfigure(0, weight=1)
        canvas_container.grid_columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.h_scrollbar.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<Configure>", lambda _: self._refresh_display())
        self.canvas.bind("<Button-1>", lambda _: self.canvas.focus_set())

        sidebar = ttk.Frame(body, style="Panel.TFrame", width=self.tokens.sidebar_width)
        sidebar.pack(side="right", fill="y", padx=(self.tokens.pad_md, 0))
        sidebar.pack_propagate(False)
        sidebar.grid_rowconfigure(0, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_columnconfigure(1, minsize=14)

        self.sidebar_canvas = tk.Canvas(
            sidebar,
            background=self.colors.panel,
            highlightthickness=0,
        )
        self.sidebar_scrollbar = ttk.Scrollbar(sidebar, orient="vertical", command=self.sidebar_canvas.yview)
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        self.sidebar_scrollbar.grid(row=0, column=1, sticky="ns")

        self.sidebar_inner = ttk.Frame(self.sidebar_canvas, style="Panel.TFrame")
        self._sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar_inner, anchor="nw")
        self.sidebar_inner.bind("<Configure>", self._on_sidebar_configure)
        self.sidebar_canvas.bind("<Configure>", self._on_sidebar_canvas_configure)
        self.master.bind_all("<MouseWheel>", self._on_sidebar_mousewheel, add=True)
        self.master.bind_all("<Button-4>", self._on_sidebar_mousewheel_linux, add=True)
        self.master.bind_all("<Button-5>", self._on_sidebar_mousewheel_linux, add=True)

        self._build_controls(self.sidebar_inner)
        self._on_sidebar_configure(None)

        status_frame = ttk.Frame(self, style="Aligner.TFrame")
        status_frame.pack(fill="x", padx=self.tokens.pad_lg, pady=(0, self.tokens.pad_sm))
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, style="Muted.TLabel")
        status_label.pack(anchor="w")

    def _build_controls(self, parent: ttk.Frame) -> None:
        control_frame = ttk.LabelFrame(parent, text="Alignment Controls", style="Aligner.TLabelframe")
        control_frame.pack(fill="x", pady=(0, self.tokens.pad_md))

        self.reference_var = tk.StringVar(value="Channel 1")
        self.active_var = tk.StringVar(value="Channel 2")
        self.step_var = tk.DoubleVar(value=1.0)
        self.coarse_var = tk.DoubleVar(value=10.0)
        self.fine_var = tk.DoubleVar(value=0.5)
        self.rot_var = tk.DoubleVar(value=0.1)
        self.opacity_var = tk.DoubleVar(value=0.5)
        self.interp_var = tk.StringVar(value="Bilinear")
        self.auto_levels_var = tk.BooleanVar(value=True)
        self.display_min_var = tk.DoubleVar(value=0.0)
        self.display_max_var = tk.DoubleVar(value=255.0)
        self.brightness_var = tk.DoubleVar(value=1.0)
        self.display_min_label_var = tk.StringVar(value="0")
        self.display_max_label_var = tk.StringVar(value="255")
        self.brightness_label_var = tk.StringVar(value="1.0x")

        self.reference_combo = self._labeled_combo(
            control_frame,
            "Reference channel",
            self.reference_var,
            [],
            help_text="Channel that stays fixed during alignment.",
        )
        self.active_combo = self._labeled_combo(
            control_frame,
            "Active channel",
            self.active_var,
            [],
            help_text="Channel you move with the keyboard.",
        )
        self._labeled_entry(
            control_frame,
            "Step size (px)",
            self.step_var,
            help_text="Base translation step in pixels for arrow keys.",
        )
        self._labeled_entry(
            control_frame,
            "Coarse multiplier",
            self.coarse_var,
            help_text="Multiplier applied when holding Shift.",
        )
        self._labeled_entry(
            control_frame,
            "Fine multiplier",
            self.fine_var,
            help_text="Multiplier applied when holding Alt.",
        )
        self._labeled_entry(
            control_frame,
            "Rotation step (deg)",
            self.rot_var,
            help_text="Degrees rotated with Q/E keys.",
        )
        self._labeled_combo(
            control_frame,
            "Interpolation",
            self.interp_var,
            list(self.resample_label_to_method),
            help_text="Resampling used for transforms and export.",
        )

        levels_frame = ttk.Frame(control_frame, style="Panel.TFrame")
        levels_frame.pack(fill="x", pady=(self.tokens.pad_sm, self.tokens.pad_md))
        levels_row = ttk.Frame(levels_frame, style="Panel.TFrame")
        levels_row.pack(fill="x")
        levels_check = ttk.Checkbutton(
            levels_row,
            text="Auto display levels",
            variable=self.auto_levels_var,
            command=self._on_levels_toggle,
            style="Panel.TCheckbutton",
        )
        levels_check.pack(side="left")
        self._add_help_icon(levels_row, "Auto-contrast preview based on channel min/max.")
        self.display_min_scale = self._labeled_slider(
            control_frame,
            "Display min",
            self.display_min_var,
            self.display_min_label_var,
            command=self._on_display_adjustment,
            help_text="Preview intensity lower bound (no effect on export).",
        )
        self.display_max_scale = self._labeled_slider(
            control_frame,
            "Display max",
            self.display_max_var,
            self.display_max_label_var,
            command=self._on_display_adjustment,
            help_text="Preview intensity upper bound (no effect on export).",
        )
        self.brightness_scale = self._labeled_slider(
            control_frame,
            "Brightness",
            self.brightness_var,
            self.brightness_label_var,
            command=self._on_display_adjustment,
            slider_from=0.4,
            slider_to=2.5,
            help_text="Preview brightness multiplier (no effect on export).",
        )
        self._set_levels_entry_state()

        opacity_frame = ttk.Frame(control_frame, style="Panel.TFrame")
        opacity_frame.pack(fill="x", pady=(0, self.tokens.pad_md))
        opacity_header = ttk.Frame(opacity_frame, style="Panel.TFrame")
        opacity_header.pack(fill="x")
        ttk.Label(opacity_header, text="Overlay opacity", style="Panel.TLabel").pack(side="left")
        self._add_help_icon(opacity_header, "Opacity of the moving channel overlay.")
        opacity_scale = ttk.Scale(
            opacity_frame,
            from_=0.05,
            to=1.0,
            orient="horizontal",
            variable=self.opacity_var,
            command=lambda _: self._schedule_render(),
        )
        opacity_scale.pack(fill="x", pady=(self.tokens.pad_sm, 0))

        zoom_frame = ttk.Frame(control_frame, style="Panel.TFrame")
        zoom_frame.pack(fill="x", pady=(0, self.tokens.pad_md))
        zoom_header = ttk.Frame(zoom_frame, style="Panel.TFrame")
        zoom_header.pack(fill="x")
        zoom_label = ttk.Label(zoom_header, text="Zoom", style="Panel.TLabel")
        zoom_label.pack(side="left")
        self._add_help_icon(zoom_header, "Zoom the preview. Mouse wheel also works.")
        zoom_label = ttk.Label(zoom_header, textvariable=self.zoom_label_var, style="PanelMuted.TLabel")
        zoom_label.pack(side="right")

        zoom_controls = ttk.Frame(zoom_frame, style="Panel.TFrame")
        zoom_controls.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        ttk.Button(zoom_controls, text="−", width=3, command=self._zoom_out, style="Secondary.TButton").pack(
            side="left"
        )
        ttk.Button(zoom_controls, text="+", width=3, command=self._zoom_in, style="Secondary.TButton").pack(
            side="left", padx=(self.tokens.pad_sm, 0)
        )
        ttk.Button(zoom_controls, text="Fit", command=self._zoom_fit, style="Secondary.TButton").pack(
            side="left", padx=(self.tokens.pad_sm, 0)
        )

        zoom_slider = ttk.Scale(
            zoom_frame,
            from_=self.tokens.zoom_min,
            to=self.tokens.zoom_max,
            orient="horizontal",
            variable=self.zoom_var,
            command=self._on_zoom_slider,
        )
        zoom_slider.pack(fill="x", pady=(self.tokens.pad_sm, 0))

        preview_frame = ttk.Frame(control_frame, style="Panel.TFrame")
        preview_frame.pack(fill="x", pady=(self.tokens.pad_sm, self.tokens.pad_md))
        preview_header = ttk.Frame(preview_frame, style="Panel.TFrame")
        preview_header.pack(fill="x")
        preview_label = ttk.Label(preview_header, text="Preview Quality", style="Panel.TLabel")
        preview_label.pack(side="left")
        self._add_help_icon(preview_header, "Lower MP is faster. Export stays full resolution.")
        ttk.Label(preview_header, textvariable=self.preview_quality_label_var, style="PanelMuted.TLabel").pack(
            side="right"
        )
        self.preview_quality_scale = ttk.Scale(
            preview_frame,
            from_=0.4,
            to=6.0,
            orient="horizontal",
            variable=self.preview_quality_var,
            command=self._on_preview_quality_change,
        )
        self.preview_quality_scale.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        fast_row = ttk.Frame(preview_frame, style="Panel.TFrame")
        fast_row.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        ttk.Checkbutton(
            fast_row,
            text="Fast preview (lower resolution)",
            variable=self.fast_preview_var,
            command=self._on_fast_preview_toggle,
            style="Panel.TCheckbutton",
        ).pack(side="left")
        self._add_help_icon(fast_row, "Force lower preview resolution for speed.")

        gpu_row = ttk.Frame(preview_frame, style="Panel.TFrame")
        gpu_row.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        ttk.Checkbutton(
            gpu_row,
            text="Use GPU for preview (experimental)",
            variable=self.use_gpu_var,
            command=self._on_gpu_toggle,
            style="Panel.TCheckbutton",
        ).pack(side="left")
        self._add_help_icon(gpu_row, "Uses OpenCV (UMat) if available; speeds transforms.")

        fullres_row = ttk.Frame(preview_frame, style="Panel.TFrame")
        fullres_row.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        ttk.Checkbutton(
            fullres_row,
            text="Full-res viewport (slow)",
            variable=self.full_res_view_var,
            command=self._on_fullres_toggle,
            style="Panel.TCheckbutton",
        ).pack(side="left")
        self._add_help_icon(fullres_row, "Render only the visible region at full resolution.")

        help_frame = ttk.LabelFrame(parent, text="Keybindings", style="Aligner.TLabelframe")
        help_frame.pack(fill="x")
        help_text = (
            "Arrow keys: move active channel\n"
            "Shift + Arrow: coarse move\n"
            "Alt + Arrow: fine move\n"
            "Q / E: rotate active channel\n"
            "Mouse wheel: zoom, drag: pan, scrollbars: navigate\n"
            "W/A/S/D or H/J/K/L: pan view (Shift = faster)\n"
            "Tab: next active channel\n"
            "R: reset active channel\n"
            "Enter: save aligned output\n"
            "Esc: cancel and quit"
        )
        ttk.Label(help_frame, text=help_text, style="Panel.TLabel", justify="left").pack(
            anchor="w", padx=self.tokens.pad_sm, pady=self.tokens.pad_sm
        )

    def _setup_fonts(self) -> None:
        base_family = self._pick_font_family(
            [
                "Avenir Next",
                "Avenir",
                "Segoe UI",
                "Helvetica Neue",
                "Inter",
                "Noto Sans",
                "DejaVu Sans",
                "Arial",
            ]
        )
        self.fonts = {
            "title": tkfont.Font(family=base_family, size=18, weight="bold"),
            "section": tkfont.Font(family=base_family, size=12, weight="bold"),
            "body": tkfont.Font(family=base_family, size=11),
            "caption": tkfont.Font(family=base_family, size=10),
            "button": tkfont.Font(family=base_family, size=11, weight="bold"),
        }
        self.master.option_add("*Font", self.fonts["body"])

    def _pick_font_family(self, preferred: List[str]) -> str:
        available = set(tkfont.families(self.master))
        for name in preferred:
            if name in available:
                return name
        return tkfont.nametofont("TkDefaultFont").actual("family")

    def _init_gpu(self) -> None:
        self.gpu_available = False
        self.gpu_status = "GPU: unavailable"
        try:
            import numpy as np  # type: ignore
            import cv2  # type: ignore

            self._np = np
            self._cv2 = cv2
            self.gpu_available = True
            self.gpu_status = "GPU: available"
        except Exception:
            self._np = None
            self._cv2 = None

    def _labeled_combo(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        values: List[str],
        help_text: Optional[str] = None,
    ) -> ttk.Combobox:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="x", pady=(0, self.tokens.pad_sm))
        header = ttk.Frame(frame, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=label, style="Panel.TLabel").pack(side="left")
        if help_text:
            self._add_help_icon(header, help_text)
        combo = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly", style="Aligner.TCombobox")
        combo.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        combo.bind("<<ComboboxSelected>>", lambda _: self._on_channel_change())
        return combo

    def _labeled_entry(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.DoubleVar,
        help_text: Optional[str] = None,
    ) -> ttk.Entry:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="x", pady=(0, self.tokens.pad_sm))
        header = ttk.Frame(frame, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=label, style="Panel.TLabel").pack(side="left")
        if help_text:
            self._add_help_icon(header, help_text)
        entry = ttk.Entry(frame, textvariable=variable, style="Aligner.TEntry")
        entry.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        entry.bind("<FocusOut>", lambda _: self._refresh_display())
        return entry

    def _labeled_slider(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.DoubleVar,
        value_label: tk.StringVar,
        command: callable,
        slider_from: float | None = None,
        slider_to: float | None = None,
        help_text: Optional[str] = None,
    ) -> ttk.Scale:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="x", pady=(0, self.tokens.pad_sm))
        header = ttk.Frame(frame, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=label, style="Panel.TLabel").pack(side="left")
        if help_text:
            self._add_help_icon(header, help_text)
        ttk.Label(header, textvariable=value_label, style="PanelMuted.TLabel").pack(side="right")
        slider = ttk.Scale(
            frame,
            from_=slider_from if slider_from is not None else variable.get(),
            to=slider_to if slider_to is not None else variable.get(),
            orient="horizontal",
            variable=variable,
            command=lambda _=None: command(),
        )
        slider.pack(fill="x", pady=(self.tokens.pad_sm, 0))
        return slider

    def _add_help_icon(self, parent: ttk.Frame, text: str) -> None:
        icon = ttk.Label(parent, text="?", style="Help.TLabel", cursor="question_arrow")
        icon.pack(side="left", padx=(6, 0))
        Tooltip(icon, text)

    def _on_levels_toggle(self) -> None:
        self._set_levels_entry_state()
        self._on_display_adjustment()

    def _set_levels_entry_state(self) -> None:
        state = "disabled" if self.auto_levels_var.get() else "normal"
        for slider in (self.display_min_scale, self.display_max_scale):
            if slider is not None:
                slider.configure(state=state)

    def _on_display_adjustment(self) -> None:
        self._update_display_labels()
        self._rebuild_display_cache()
        self._render_view(draft=False)

    def _update_display_labels(self) -> None:
        self.display_min_label_var.set(f"{self.display_min_var.get():.0f}")
        self.display_max_label_var.set(f"{self.display_max_var.get():.0f}")
        self.brightness_label_var.set(f"{self.brightness_var.get():.2f}x")

    def _on_preview_quality_change(self, _: str | None = None) -> None:
        self._update_preview_quality_label()
        if self.fast_preview_var.get():
            return
        self._rebuild_preview_cache()
        self._render_view(draft=False)

    def _on_fullres_toggle(self) -> None:
        if self.full_res_view_var.get():
            self._set_status("Full-res viewport enabled (preview may be slower).")
        self._render_view(draft=False)

    def _on_fast_preview_toggle(self) -> None:
        if self.fast_preview_var.get():
            self._preview_quality_backup = float(self.preview_quality_var.get())
            self.preview_quality_var.set(0.5)
            if self.preview_quality_scale is not None:
                self.preview_quality_scale.configure(state="disabled")
        else:
            if self.preview_quality_scale is not None:
                self.preview_quality_scale.configure(state="normal")
            if self._preview_quality_backup is not None:
                self.preview_quality_var.set(self._preview_quality_backup)
        self._update_preview_quality_label()
        self._rebuild_preview_cache()
        self._render_view(draft=False)

    def _on_gpu_toggle(self) -> None:
        if self.use_gpu_var.get() and not self.gpu_available:
            messagebox.showinfo(
                "GPU preview unavailable",
                "GPU preview requires numpy + opencv-python. "
                "Install them in the venv and restart the app.",
            )
            self.use_gpu_var.set(False)
        self._render_view(draft=False)

    def _update_preview_quality_label(self) -> None:
        mp = float(self.preview_quality_var.get())
        self.preview_quality_label_var.set(f"Preview: {mp:.1f} MP")

    def _preview_target_pixels(self) -> float:
        mp = float(self.preview_quality_var.get())
        return max(0.1, mp) * 1_000_000

    def _rebuild_preview_cache(self) -> None:
        if not self.channels:
            return
        self.preview_channels = self._build_preview_channels(self.channels)
        self._rebuild_display_cache()
        self._needs_center_view = True
        self._overlay_cache_key = None
        self._overlay_cache = None

    def _on_zoom_slider(self, _: str | None = None) -> None:
        self._clamp_zoom()
        self._update_zoom_label()
        self._schedule_render()

    def _zoom_in(self) -> None:
        self.zoom_var.set(self.zoom_var.get() + self.tokens.zoom_step)
        self._on_zoom_slider()

    def _zoom_out(self) -> None:
        self.zoom_var.set(self.zoom_var.get() - self.tokens.zoom_step)
        self._on_zoom_slider()

    def _zoom_fit(self) -> None:
        self.zoom_var.set(1.0)
        self._needs_center_view = True
        self._on_zoom_slider()

    def _clamp_zoom(self) -> None:
        value = float(self.zoom_var.get())
        value = max(self.tokens.zoom_min, min(self.tokens.zoom_max, value))
        self.zoom_var.set(value)

    def _update_zoom_label(self) -> None:
        percent = int(round(float(self.zoom_var.get()) * 100))
        self.zoom_label_var.set(f"Zoom: {percent}%")

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.delta == 0:
            return
        step = self.tokens.zoom_step if event.delta > 0 else -self.tokens.zoom_step
        self.zoom_var.set(self.zoom_var.get() + step)
        self._on_zoom_slider()

    def _on_mousewheel_linux(self, event: tk.Event) -> None:
        if getattr(event, "num", 0) == 4:
            self.zoom_var.set(self.zoom_var.get() + self.tokens.zoom_step)
        elif getattr(event, "num", 0) == 5:
            self.zoom_var.set(self.zoom_var.get() - self.tokens.zoom_step)
        else:
            return
        self._on_zoom_slider()

    def _on_scroll(self, axis: str, *args: str) -> None:
        if axis == "x":
            self.canvas.xview(*args)
        else:
            self.canvas.yview(*args)
        self._schedule_render()

    def _on_sidebar_configure(self, _: tk.Event) -> None:
        if not self.sidebar_canvas:
            return
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _on_sidebar_canvas_configure(self, event: tk.Event) -> None:
        if not self.sidebar_canvas or self._sidebar_window is None:
            return
        self.sidebar_canvas.itemconfigure(self._sidebar_window, width=event.width)
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _on_sidebar_mousewheel(self, event: tk.Event) -> str:
        if not self.sidebar_canvas or not self.sidebar_inner:
            return "break"
        if not self._is_descendant(event.widget, self.sidebar_inner) and event.widget is not self.sidebar_canvas:
            return ""
        if event.delta == 0:
            return "break"
        self.sidebar_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _on_sidebar_mousewheel_linux(self, event: tk.Event) -> str:
        if not self.sidebar_canvas or not self.sidebar_inner:
            return "break"
        if not self._is_descendant(event.widget, self.sidebar_inner) and event.widget is not self.sidebar_canvas:
            return ""
        if getattr(event, "num", 0) == 4:
            self.sidebar_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", 0) == 5:
            self.sidebar_canvas.yview_scroll(1, "units")
        return "break"

    def _is_descendant(self, widget: tk.Widget, ancestor: tk.Widget) -> bool:
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    def _start_pan(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self._pan_anchor = (event.x, event.y)
        self.canvas.scan_mark(event.x, event.y)

    def _do_pan(self, event: tk.Event) -> None:
        if not self._pan_anchor:
            self._pan_anchor = (event.x, event.y)
            self.canvas.scan_mark(event.x, event.y)
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self._schedule_render()

    def _end_pan(self, _: tk.Event) -> None:
        self._pan_anchor = None

    def _bind_keys(self) -> None:
        self.master.bind_all("<Escape>", lambda _: self._quit(), add=True)
        self.master.bind_all("<Return>", lambda _: self._save_aligned(), add=True)
        self.master.bind_all("<Tab>", self._cycle_active_channel, add=True)
        self.master.bind_all("<KeyPress-r>", lambda _: self._reset_active(), add=True)
        self.master.bind_all("<KeyPress-R>", lambda _: self._reset_active(), add=True)
        self.master.bind_all("<KeyPress-q>", lambda e: self._rotate(-1, e), add=True)
        self.master.bind_all("<KeyPress-Q>", lambda e: self._rotate(-1, e), add=True)
        self.master.bind_all("<KeyPress-e>", lambda e: self._rotate(1, e), add=True)
        self.master.bind_all("<KeyPress-E>", lambda e: self._rotate(1, e), add=True)
        self.master.bind_all("<Left>", lambda e: self._move(-1, 0, e), add=True)
        self.master.bind_all("<Right>", lambda e: self._move(1, 0, e), add=True)
        self.master.bind_all("<Up>", lambda e: self._move(0, -1, e), add=True)
        self.master.bind_all("<Down>", lambda e: self._move(0, 1, e), add=True)
        self.master.bind_all("<KeyPress-w>", lambda e: self._pan_key(0, -1, e), add=True)
        self.master.bind_all("<KeyPress-W>", lambda e: self._pan_key(0, -1, e), add=True)
        self.master.bind_all("<KeyPress-s>", lambda e: self._pan_key(0, 1, e), add=True)
        self.master.bind_all("<KeyPress-S>", lambda e: self._pan_key(0, 1, e), add=True)
        self.master.bind_all("<KeyPress-a>", lambda e: self._pan_key(-1, 0, e), add=True)
        self.master.bind_all("<KeyPress-A>", lambda e: self._pan_key(-1, 0, e), add=True)
        self.master.bind_all("<KeyPress-d>", lambda e: self._pan_key(1, 0, e), add=True)
        self.master.bind_all("<KeyPress-D>", lambda e: self._pan_key(1, 0, e), add=True)
        self.master.bind_all("<KeyPress-h>", lambda e: self._pan_key(-1, 0, e), add=True)
        self.master.bind_all("<KeyPress-H>", lambda e: self._pan_key(-1, 0, e), add=True)
        self.master.bind_all("<KeyPress-j>", lambda e: self._pan_key(0, 1, e), add=True)
        self.master.bind_all("<KeyPress-J>", lambda e: self._pan_key(0, 1, e), add=True)
        self.master.bind_all("<KeyPress-k>", lambda e: self._pan_key(0, -1, e), add=True)
        self.master.bind_all("<KeyPress-K>", lambda e: self._pan_key(0, -1, e), add=True)
        self.master.bind_all("<KeyPress-l>", lambda e: self._pan_key(1, 0, e), add=True)
        self.master.bind_all("<KeyPress-L>", lambda e: self._pan_key(1, 0, e), add=True)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind("<Button-5>", self._on_mousewheel_linux)
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._do_pan)
        self.canvas.bind("<ButtonRelease-1>", self._end_pan)

    def _configure_drag_drop(self) -> None:
        if not DND_AVAILABLE:
            self.dnd_enabled = False
            return
        self.dnd_enabled = True
        self._register_drop_targets(self.master)
        if not self.dnd_enabled:
            self._set_status("Drag & drop disabled. Use Open Images.")

    def _on_drop(self, event: tk.Event) -> str:
        paths = parse_drop_files(str(getattr(event, "data", "")))
        paths = [p for p in paths if os.path.exists(p) and os.path.isfile(p)]
        image_paths = [p for p in paths if self._is_supported_image(p)]

        if not image_paths:
            messagebox.showerror(
                "Unsupported files",
                "Drop image files (tif, tiff, png, jpg, jpeg, bmp).",
            )
            return "break"

        self.load_images(image_paths)
        return "break"

    def _is_supported_image(self, path: str) -> bool:
        return Path(path).suffix.lower() in self.SUPPORTED_EXTENSIONS

    def _register_drop_targets(self, widget: tk.Widget) -> None:
        if hasattr(widget, "drop_target_register"):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except tk.TclError:
                self.dnd_enabled = False
        for child in widget.winfo_children():
            self._register_drop_targets(child)

    def _open_images_dialog(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Open channel images",
            filetypes=[
                ("Image files", "*.tif *.tiff *.png *.jpg *.jpeg *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if paths:
            self.load_images(list(paths))

    def load_images(self, paths: List[str]) -> None:
        try:
            stack = load_channels_from_paths(paths)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self._render_empty_state()
            return

        if len(stack.channels) < 2:
            messagebox.showerror("Load failed", "Provide at least two channels to align.")
            self._render_empty_state()
            return

        self.channels = stack.channels
        self.preview_channels = self._build_preview_channels(self.channels)
        self.transforms = [TransformState() for _ in self.channels]
        self.reference_index = 0
        self.active_index = 1
        self.last_save_path = None
        self.source_paths = stack.source_paths
        self.tiffinfo = stack.tiffinfo
        self.save_kwargs = stack.save_kwargs
        self._full_auto_ranges = None
        min_val, max_val = self._infer_bit_depth_range(self.channels[0])
        self.display_min_var.set(min_val)
        self.display_max_var.set(max_val)
        self._set_levels_entry_state()
        self._update_display_slider_ranges(min_val, max_val)
        self._update_display_labels()
        self._update_preview_quality_label()
        self._rebuild_display_cache()
        self._needs_center_view = True

        self._update_channel_choices()
        self._refresh_display()
        self._set_status(self._status_with_preview_scale("Images loaded. Use arrow keys to align."))
        if self.canvas is not None:
            self.canvas.focus_set()

    def _update_channel_choices(self) -> None:
        values = [f"Channel {idx + 1}" for idx in range(len(self.channels))]
        self.reference_var.set(values[self.reference_index])
        self.active_var.set(values[self.active_index])

        if self.reference_combo is not None:
            self.reference_combo.configure(values=values)
        if self.active_combo is not None:
            self.active_combo.configure(values=values)

    def _on_channel_change(self) -> None:
        if not self.channels:
            return

        ref_idx = self._channel_index_from_var(self.reference_var)
        active_idx = self._channel_index_from_var(self.active_var)

        if active_idx == ref_idx:
            active_idx = (ref_idx + 1) % len(self.channels)
            self.active_var.set(f"Channel {active_idx + 1}")

        self.reference_index = ref_idx
        self.active_index = active_idx
        self._refresh_display()
        self._set_status(self._status_with_preview_scale("Reference or active channel updated."))

    def _channel_index_from_var(self, var: tk.StringVar) -> int:
        value = var.get().replace("Channel", "").strip()
        try:
            idx = int(value) - 1
        except ValueError:
            idx = 0
        return max(0, min(idx, len(self.channels) - 1))

    def _move(self, dx: float, dy: float, event: tk.Event) -> None:
        if not self._has_channels():
            return
        step = self._scaled_step(event)
        state = self.transforms[self.active_index]
        state.dx += dx * step
        state.dy += dy * step
        self._schedule_render()
        self._update_status_for_active()

    def _rotate(self, direction: int, event: tk.Event) -> None:
        if not self._has_channels():
            return
        rot = self._scaled_rot(event)
        state = self.transforms[self.active_index]
        state.angle_deg += direction * rot
        self._schedule_render()
        self._update_status_for_active()

    def _scaled_step(self, event: tk.Event) -> float:
        step = self._safe_float(self.step_var, 1.0)
        if self._is_shift(event):
            step *= self._safe_float(self.coarse_var, 10.0)
        elif self._is_alt(event):
            step *= self._safe_float(self.fine_var, 0.5)
        return step

    def _scaled_rot(self, event: tk.Event) -> float:
        rot = self._safe_float(self.rot_var, 0.1)
        if self._is_shift(event):
            rot *= 10.0
        elif self._is_alt(event):
            rot *= 0.2
        return rot

    def _safe_float(self, var: tk.DoubleVar, fallback: float, allow_zero: bool = False) -> float:
        try:
            value = float(var.get())
        except tk.TclError:
            return fallback
        if value == 0 and not allow_zero:
            return fallback
        return value

    def _should_ignore_key(self) -> bool:
        widget = self.master.focus_get()
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return True
        return False

    def _pan_key(self, dx: int, dy: int, event: tk.Event) -> None:
        if self._should_ignore_key():
            return
        step_x = max(20, int(self.canvas.winfo_width() * 0.05))
        step_y = max(20, int(self.canvas.winfo_height() * 0.05))
        if self._is_shift(event):
            step_x *= 3
            step_y *= 3
        self._pan_by_pixels(dx * step_x, dy * step_y)
        self._schedule_render()

    def _pan_by_pixels(self, dx: int, dy: int) -> None:
        if self._scroll_w <= 0 or self._scroll_h <= 0:
            return
        if self._scroll_w > self._canvas_w:
            x_start, x_end = self.canvas.xview()
            max_start = max(0.0, 1.0 - (self._canvas_w / self._scroll_w))
            new_start = min(max(x_start + dx / self._scroll_w, 0.0), max_start)
            self.canvas.xview_moveto(new_start)
        if self._scroll_h > self._canvas_h:
            y_start, y_end = self.canvas.yview()
            max_start = max(0.0, 1.0 - (self._canvas_h / self._scroll_h))
            new_start = min(max(y_start + dy / self._scroll_h, 0.0), max_start)
            self.canvas.yview_moveto(new_start)

    def _update_display_slider_ranges(self, min_val: float, max_val: float) -> None:
        if self.display_min_scale is not None:
            self.display_min_scale.configure(from_=min_val, to=max_val - 1)
        if self.display_max_scale is not None:
            self.display_max_scale.configure(from_=min_val + 1, to=max_val)

    def _infer_bit_depth_range(self, image: Image.Image) -> tuple[float, float]:
        mode = image.mode or ""
        if "16" in mode or mode.startswith("I;16") or mode == "I":
            return (0.0, 65535.0)
        if mode == "F":
            extrema = image.getextrema()
            if extrema and isinstance(extrema, tuple):
                min_val, max_val = float(extrema[0]), float(extrema[1])
                if max_val <= min_val:
                    max_val = min_val + 1.0
                return (min_val, max_val)
        return (0.0, 255.0)

    def _display_range(self) -> Optional[tuple[float, float]]:
        if self.auto_levels_var.get():
            return None
        min_val = self._safe_float(self.display_min_var, 0.0, allow_zero=True)
        max_val = self._safe_float(self.display_max_var, 255.0, allow_zero=True)
        if max_val <= min_val:
            return None
        return (min_val, max_val)

    def _auto_display_range(self, image: Image.Image) -> Optional[tuple[float, float]]:
        extrema = image.getextrema()
        if extrema and isinstance(extrema, tuple):
            min_val, max_val = float(extrema[0]), float(extrema[1])
            if max_val <= min_val:
                return None
            return (min_val, max_val)
        return None

    def _auto_display_range_for_index(self, index: int, use_full: bool) -> Optional[tuple[float, float]]:
        if use_full:
            if self._full_auto_ranges is None or len(self._full_auto_ranges) != len(self.channels):
                self._full_auto_ranges = [self._auto_display_range(channel) for channel in self.channels]
            if self._full_auto_ranges and index < len(self._full_auto_ranges):
                return self._full_auto_ranges[index]
            return None
        if index < len(self.preview_channels):
            return self._auto_display_range(self.preview_channels[index])
        return None

    def _rebuild_display_cache(self) -> None:
        if not self.preview_channels:
            self.display_channels = []
            self.reference_rgb_cache = []
            return
        self.display_channels = []
        self.reference_rgb_cache = []
        self._display_cache_version += 1
        self._overlay_cache_key = None
        self._overlay_cache = None
        manual_range = self._display_range()
        brightness = float(self.brightness_var.get())
        for channel in self.preview_channels:
            display_range = manual_range if manual_range is not None else self._auto_display_range(channel)
            gray = to_display_gray(channel, display_range=display_range)
            if abs(brightness - 1.0) > 0.01:
                gray = ImageEnhance.Brightness(gray).enhance(brightness)
            self.display_channels.append(gray)
            base = Image.merge("RGB", (gray, gray, gray))
            self.reference_rgb_cache.append(base)

    def _viewport_geometry(
        self,
        base_size: tuple[int, int],
        scale: float,
        canvas_w: int,
        canvas_h: int,
        scroll_w: int,
        scroll_h: int,
        x0: float,
        y0: float,
    ) -> Optional[tuple[int, int, int, int, float, float, float, float]]:
        disp_w = max(int(base_size[0] * scale), 1)
        disp_h = max(int(base_size[1] * scale), 1)
        offset_x = max((scroll_w - disp_w) // 2, 0)
        offset_y = max((scroll_h - disp_h) // 2, 0)

        img_x0 = x0 - offset_x
        img_y0 = y0 - offset_y
        img_x1 = img_x0 + canvas_w
        img_y1 = img_y0 + canvas_h

        vis_x0 = max(0.0, img_x0)
        vis_y0 = max(0.0, img_y0)
        vis_x1 = min(float(disp_w), img_x1)
        vis_y1 = min(float(disp_h), img_y1)

        if vis_x1 <= vis_x0 or vis_y1 <= vis_y0:
            return None
        return (disp_w, disp_h, offset_x, offset_y, vis_x0, vis_y0, vis_x1, vis_y1)

    def _transform_crop(
        self,
        image: Image.Image,
        state: TransformState,
        out_x0: int,
        out_y0: int,
        out_w: int,
        out_h: int,
        resample: int,
    ) -> Image.Image:
        if not state.dx and not state.dy and not state.angle_deg:
            return image.crop((out_x0, out_y0, out_x0 + out_w, out_y0 + out_h))
        matrix = affine_matrix_for_crop(state, image.size, out_x0, out_y0)
        return image.transform((out_w, out_h), Image.AFFINE, matrix, resample=resample, fillcolor=0)

    def _transform_preview(self, image: Image.Image, state: TransformState) -> Image.Image:
        if self.use_gpu_var.get() and self.gpu_available and getattr(self, "_cv2", None):
            try:
                return self._transform_preview_cv2(image, state)
            except Exception:
                return apply_transform(image, state, Image.BILINEAR)
        return apply_transform(image, state, Image.BILINEAR)

    def _transform_preview_cv2(self, image: Image.Image, state: TransformState) -> Image.Image:
        cv2 = self._cv2
        np = self._np
        if cv2 is None or np is None:
            return apply_transform(image, state, Image.BILINEAR)
        arr = np.array(image, dtype=np.uint8)
        height, width = arr.shape[:2]
        center = (width / 2.0, height / 2.0)
        matrix = cv2.getRotationMatrix2D(center, float(state.angle_deg), 1.0)
        matrix[0, 2] += float(state.dx)
        matrix[1, 2] += float(state.dy)
        src = cv2.UMat(arr) if hasattr(cv2, "UMat") else arr
        warped = cv2.warpAffine(
            src,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        if hasattr(warped, "get"):
            warped = warped.get()
        warped = warped.astype("uint8", copy=False)
        return Image.fromarray(warped, mode="L")

    def _is_shift(self, event: tk.Event) -> bool:
        return bool(event.state & 0x0001)

    def _is_alt(self, event: tk.Event) -> bool:
        return bool(event.state & 0x0008)

    def _cycle_active_channel(self, _: tk.Event) -> str:
        if not self._has_channels():
            return "break"
        self.active_index = (self.active_index + 1) % len(self.channels)
        if self.active_index == self.reference_index:
            self.active_index = (self.active_index + 1) % len(self.channels)
        self.active_var.set(f"Channel {self.active_index + 1}")
        self._refresh_display()
        self._update_status_for_active()
        return "break"

    def _reset_active(self) -> None:
        if not self._has_channels():
            return
        self.transforms[self.active_index] = TransformState()
        self._refresh_display()
        self._update_status_for_active()

    def _reset_all(self) -> None:
        if not self._has_channels():
            return
        self.transforms = [TransformState() for _ in self.transforms]
        self._refresh_display()
        self._set_status(self._status_with_preview_scale("All transforms reset."))

    def _save_aligned(self) -> None:
        if not self._has_channels():
            return

        initial_dir = os.path.dirname(self.source_paths[0]) if self.source_paths else None
        output_path = None
        while True:
            output_path = filedialog.asksaveasfilename(
                title="Save aligned channels",
                defaultextension=".tif",
                initialdir=initial_dir,
                initialfile=self._default_output_name(),
                filetypes=[("TIFF", "*.tif *.tiff"), ("All files", "*.*")],
            )
            if not output_path:
                return
            if self._is_output_conflict(output_path):
                messagebox.showerror(
                    "Save failed",
                    "Pick a new filename. Original images are never overwritten.",
                )
                continue
            if os.path.exists(output_path):
                messagebox.showerror(
                    "Save failed",
                    "File already exists. Choose a new name to avoid overwriting.",
                )
                continue
            break

        try:
            aligned = []
            for idx, channel in enumerate(self.channels):
                if idx == self.reference_index:
                    aligned.append(channel.copy())
                else:
                    aligned.append(apply_transform(channel, self.transforms[idx], self._resample_method()))
            tiffinfo = add_alignment_tag(self.tiffinfo)
            save_channels(aligned, output_path, tiffinfo=tiffinfo, save_kwargs=self.save_kwargs)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return

        self.last_save_path = output_path
        self._set_status(f"Saved aligned stack (Manual Aligned tag): {os.path.basename(output_path)}")

    def _default_output_name(self) -> str:
        if self.last_save_path:
            return os.path.basename(self.last_save_path)
        if self.source_paths:
            base = Path(self.source_paths[0]).stem
            return f"{base}_manual_aligned.tif"
        return "manual_aligned.tif"

    def _is_output_conflict(self, output_path: str) -> bool:
        output_abs = os.path.abspath(output_path)
        for src in self.source_paths:
            if os.path.abspath(src) == output_abs:
                return True
        return False

    def _resample_method(self) -> int:
        return self.resample_label_to_method.get(self.interp_var.get(), Image.BILINEAR)

    def _compute_preview_scale(self, size: tuple[int, int]) -> float:
        width, height = size
        if width <= 0 or height <= 0:
            return 1.0
        max_dim = max(width, height)
        scale_dim = min(1.0, self.tokens.preview_max_dim / max_dim)
        max_pixels = self._preview_target_pixels()
        scale_area = min(1.0, math.sqrt(max_pixels / float(width * height)))
        scale = min(scale_dim, scale_area, 1.0)
        return max(scale, 0.05)

    def _build_preview_channels(self, channels: List[Image.Image]) -> List[Image.Image]:
        if not channels:
            self.preview_scale = 1.0
            return []
        self.preview_scale = self._compute_preview_scale(channels[0].size)
        if self.preview_scale >= 0.999:
            return channels
        preview = []
        for channel in channels:
            width, height = channel.size
            new_size = (max(int(width * self.preview_scale), 1), max(int(height * self.preview_scale), 1))
            preview.append(channel.resize(new_size, resample=Image.BILINEAR))
        return preview

    def _scaled_state(self, state: TransformState) -> TransformState:
        if self.preview_scale >= 0.999:
            return state
        return TransformState(
            dx=state.dx * self.preview_scale,
            dy=state.dy * self.preview_scale,
            angle_deg=state.angle_deg,
        )

    def _status_with_preview_scale(self, message: str) -> str:
        if self.preview_scale >= 0.999:
            return message
        percent = int(round(self.preview_scale * 100))
        return f"{message} Preview: {percent}% (export full res)."

    def _schedule_render(self) -> None:
        self._render_view(draft=True)
        if self._render_job is not None:
            self.after_cancel(self._render_job)
        self._render_job = self.after(120, self._render_final)

    def _render_final(self) -> None:
        self._render_job = None
        self._render_view(draft=False)

    def _render_view(self, draft: bool = False) -> None:
        if not self.channels:
            self._render_empty_state()
            return
        if self.full_res_view_var.get():
            self._render_fullres_view(draft=draft)
            return
        self._clamp_zoom()
        self._update_zoom_label()

        if not self.display_channels or not self.reference_rgb_cache:
            self._rebuild_display_cache()

        key = (
            self.reference_index,
            self.active_index,
            round(self.transforms[self.active_index].dx, 4),
            round(self.transforms[self.active_index].dy, 4),
            round(self.transforms[self.active_index].angle_deg, 4),
            round(float(self.opacity_var.get()), 4),
            self._display_cache_version,
        )
        if self._overlay_cache_key != key or self._overlay_cache is None:
            reference_rgb = self.reference_rgb_cache[self.reference_index]
            active_gray = self.display_channels[self.active_index]
            moved = self._transform_preview(active_gray, self._scaled_state(self.transforms[self.active_index]))
            overlay_rgb = ImageOps.colorize(moved, black=(0, 0, 0), white=(240, 90, 90))
            alpha_value = int(max(0.0, min(float(self.opacity_var.get()), 1.0)) * 255)
            alpha = Image.new("L", moved.size, alpha_value)
            overlay = overlay_rgb.copy()
            overlay.putalpha(alpha)
            composed = reference_rgb.convert("RGBA")
            composed.alpha_composite(overlay)
            self._overlay_cache = composed.convert("RGB")
            self._overlay_cache_key = key

        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        fit_scale = compute_fit_scale(self._overlay_cache.size, (canvas_w, canvas_h))
        zoom = float(self.zoom_var.get())
        scale = max(fit_scale * zoom, 0.05)
        disp_w = max(int(self._overlay_cache.size[0] * scale), 1)
        disp_h = max(int(self._overlay_cache.size[1] * scale), 1)
        scroll_w = max(canvas_w, disp_w)
        scroll_h = max(canvas_h, disp_h)

        if self._needs_center_view:
            x_fraction = 0.0
            y_fraction = 0.0
            if scroll_w > canvas_w:
                x_fraction = max((scroll_w - canvas_w) / 2.0, 0.0) / scroll_w
            if scroll_h > canvas_h:
                y_fraction = max((scroll_h - canvas_h) / 2.0, 0.0) / scroll_h
        else:
            xview = self.canvas.xview()
            yview = self.canvas.yview()
            x_fraction = clamp_scroll_fraction(xview[0] if xview else 0.0, scroll_w, canvas_w)
            y_fraction = clamp_scroll_fraction(yview[0] if yview else 0.0, scroll_h, canvas_h)

        x0 = x_fraction * scroll_w
        y0 = y_fraction * scroll_h

        rendered = self._render_viewport(
            self._overlay_cache,
            scale=scale,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            scroll_w=scroll_w,
            scroll_h=scroll_h,
            x0=x0,
            y0=y0,
            draft=draft,
        )
        if rendered is None:
            return
        overlay, pos_x, pos_y = rendered
        self._scroll_w = scroll_w
        self._scroll_h = scroll_h
        self._canvas_w = canvas_w
        self._canvas_h = canvas_h

        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(overlay)
        self.canvas.create_image(pos_x, pos_y, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))
        self.canvas.xview_moveto(x_fraction)
        self.canvas.yview_moveto(y_fraction)
        if self._needs_center_view:
            self._needs_center_view = False

    def _render_viewport(
        self,
        base: Image.Image,
        *,
        scale: float,
        canvas_w: int,
        canvas_h: int,
        scroll_w: int,
        scroll_h: int,
        x0: float,
        y0: float,
        draft: bool = False,
    ) -> Optional[tuple[Image.Image, int, int]]:
        geometry = self._viewport_geometry(base.size, scale, canvas_w, canvas_h, scroll_w, scroll_h, x0, y0)
        if geometry is None:
            return (Image.new("RGB", (1, 1), self.colors.canvas_bg), int(x0), int(y0))
        _, _, offset_x, offset_y, vis_x0, vis_y0, vis_x1, vis_y1 = geometry

        base_x0 = int(vis_x0 / scale)
        base_y0 = int(vis_y0 / scale)
        base_x1 = min(int(math.ceil(vis_x1 / scale)), base.size[0])
        base_y1 = min(int(math.ceil(vis_y1 / scale)), base.size[1])

        crop = base.crop((base_x0, base_y0, base_x1, base_y1))
        vis_w = int(vis_x1 - vis_x0)
        vis_h = int(vis_y1 - vis_y0)
        resample = Image.NEAREST if draft else Image.BILINEAR
        crop = crop.resize((vis_w, vis_h), resample=resample)
        pos_x = int(offset_x + vis_x0)
        pos_y = int(offset_y + vis_y0)
        return (crop, pos_x, pos_y)

    def _render_fullres_view(self, draft: bool = False) -> None:
        if not self.channels:
            self._render_empty_state()
            return
        self._clamp_zoom()
        self._update_zoom_label()

        base_size = self.channels[0].size
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        fit_scale = compute_fit_scale(base_size, (canvas_w, canvas_h))
        zoom = float(self.zoom_var.get())
        scale = max(fit_scale * zoom, 0.05)
        disp_w = max(int(base_size[0] * scale), 1)
        disp_h = max(int(base_size[1] * scale), 1)
        scroll_w = max(canvas_w, disp_w)
        scroll_h = max(canvas_h, disp_h)

        if self._needs_center_view:
            x_fraction = 0.0
            y_fraction = 0.0
            if scroll_w > canvas_w:
                x_fraction = max((scroll_w - canvas_w) / 2.0, 0.0) / scroll_w
            if scroll_h > canvas_h:
                y_fraction = max((scroll_h - canvas_h) / 2.0, 0.0) / scroll_h
        else:
            xview = self.canvas.xview()
            yview = self.canvas.yview()
            x_fraction = clamp_scroll_fraction(xview[0] if xview else 0.0, scroll_w, canvas_w)
            y_fraction = clamp_scroll_fraction(yview[0] if yview else 0.0, scroll_h, canvas_h)

        x0 = x_fraction * scroll_w
        y0 = y_fraction * scroll_h

        geometry = self._viewport_geometry(base_size, scale, canvas_w, canvas_h, scroll_w, scroll_h, x0, y0)
        if geometry is None:
            self.canvas.delete("all")
            return
        _, _, offset_x, offset_y, vis_x0, vis_y0, vis_x1, vis_y1 = geometry

        base_x0 = int(vis_x0 / scale)
        base_y0 = int(vis_y0 / scale)
        base_x1 = min(int(math.ceil(vis_x1 / scale)), base_size[0])
        base_y1 = min(int(math.ceil(vis_y1 / scale)), base_size[1])

        ref_channel = self.channels[self.reference_index]
        active_channel = self.channels[self.active_index]
        state = self.transforms[self.active_index]

        manual_range = self._display_range()
        ref_range = manual_range
        active_range = manual_range
        if manual_range is None:
            ref_range = self._auto_display_range_for_index(self.reference_index, use_full=True)
            active_range = self._auto_display_range_for_index(self.active_index, use_full=True)
        brightness = float(self.brightness_var.get())

        ref_crop = ref_channel.crop((base_x0, base_y0, base_x1, base_y1))
        ref_gray = to_display_gray(ref_crop, display_range=ref_range)
        if abs(brightness - 1.0) > 0.01:
            ref_gray = ImageEnhance.Brightness(ref_gray).enhance(brightness)
        ref_rgb = Image.merge("RGB", (ref_gray, ref_gray, ref_gray))

        crop_w = max(base_x1 - base_x0, 1)
        crop_h = max(base_y1 - base_y0, 1)
        resample = Image.NEAREST if draft else Image.BILINEAR
        active_crop = self._transform_crop(active_channel, state, base_x0, base_y0, crop_w, crop_h, resample=resample)
        active_gray = to_display_gray(active_crop, display_range=active_range)
        if abs(brightness - 1.0) > 0.01:
            active_gray = ImageEnhance.Brightness(active_gray).enhance(brightness)

        overlay_rgb = ImageOps.colorize(active_gray, black=(0, 0, 0), white=(240, 90, 90))
        alpha_value = int(max(0.0, min(float(self.opacity_var.get()), 1.0)) * 255)
        alpha = Image.new("L", active_gray.size, alpha_value)
        overlay = overlay_rgb.copy()
        overlay.putalpha(alpha)
        composed = ref_rgb.convert("RGBA")
        composed.alpha_composite(overlay)
        composed = composed.convert("RGB")

        vis_w = max(int(vis_x1 - vis_x0), 1)
        vis_h = max(int(vis_y1 - vis_y0), 1)
        if composed.size != (vis_w, vis_h):
            composed = composed.resize((vis_w, vis_h), resample=Image.NEAREST if draft else Image.BILINEAR)

        self._scroll_w = scroll_w
        self._scroll_h = scroll_h
        self._canvas_w = canvas_w
        self._canvas_h = canvas_h

        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(composed)
        pos_x = int(offset_x + vis_x0)
        pos_y = int(offset_y + vis_y0)
        self.canvas.create_image(pos_x, pos_y, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))
        self.canvas.xview_moveto(x_fraction)
        self.canvas.yview_moveto(y_fraction)
        if self._needs_center_view:
            self._needs_center_view = False

    def _refresh_display(self) -> None:
        self._render_view(draft=False)

    def _scale_for_zoom(self, image: Image.Image) -> Image.Image:
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        fit_scale = compute_fit_scale(image.size, (canvas_w, canvas_h))
        zoom = float(self.zoom_var.get())
        scale = max(fit_scale * zoom, 0.05)
        new_w = max(int(image.size[0] * scale), 1)
        new_h = max(int(image.size[1] * scale), 1)
        if (new_w, new_h) == image.size:
            return image
        return image.resize((new_w, new_h), resample=Image.BILINEAR)

    def _center_view(self, scroll_w: int, scroll_h: int, canvas_w: int, canvas_h: int) -> None:
        if scroll_w <= 0 or scroll_h <= 0:
            return
        if scroll_w <= canvas_w:
            x_fraction = 0.0
        else:
            x0 = max((scroll_w - canvas_w) / 2.0, 0.0)
            x_fraction = x0 / scroll_w
        if scroll_h <= canvas_h:
            y_fraction = 0.0
        else:
            y0 = max((scroll_h - canvas_h) / 2.0, 0.0)
            y_fraction = y0 / scroll_h
        self.canvas.xview_moveto(x_fraction)
        self.canvas.yview_moveto(y_fraction)

    def _render_empty_state(self) -> None:
        self.canvas.delete("all")
        message = "Drop images here or click Open Images\n(2+ channels required)"
        if not self.dnd_enabled:
            message += "\\nDrag & drop disabled (install tkinterdnd2)"
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text=message,
            fill=self.colors.highlight,
            font=self.fonts["section"],
        )
        self._set_status("No images loaded.")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _update_status_for_active(self) -> None:
        state = self.transforms[self.active_index]
        msg = (
            f"Active C{self.active_index + 1} | dx={state.dx:.2f} "
            f"dy={state.dy:.2f} angle={state.angle_deg:.2f} deg"
        )
        self._set_status(self._status_with_preview_scale(msg))

    def _has_channels(self) -> bool:
        return bool(self.channels)

    def _quit(self) -> None:
        self.master.destroy()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual channel alignment app.")
    parser.add_argument("paths", nargs="*", help="Input images or multi-page TIFF")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = ManualChannelAlignerApp(root, paths=args.paths or None)
    app.mainloop()


if __name__ == "__main__":
    main()
