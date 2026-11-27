"""
gui/zoom_helper.py

Reusable Zoom helper for Tkinter Text and ScrolledText widgets.
Features:
  - Range: 50% to 300% in 10% steps, default 100%.
  - Controls: Ctrl+Wheel, touchpad pinch, Ctrl+Plus/Equal, Ctrl+Minus, Ctrl+0.
  - Accumulates wheel deltas so high-resolution touchpads are not jumpy.
  - Preserves visible line position on zoom (no jump to top) and re-wraps lines.
  - Does not change file contents or mark text modified.
  - Session-only (resets when dialog/app restarts).
  - Can be attached to any Text area across the application.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable


class TextViewerZoomer:
    """Manages zooming for a target Tkinter Text or ScrolledText widget."""

    MIN_ZOOM = 50
    MAX_ZOOM = 300
    ZOOM_STEP = 10
    DEFAULT_ZOOM = 100

    def __init__(
        self,
        text_widget: tk.Text,
        base_font_family: str = "Consolas",
        base_font_size: int = 10,
        on_zoom_changed: Optional[Callable[[int], None]] = None,
    ):
        self.text_widget = text_widget
        self.base_font_family = base_font_family
        self.base_font_size = base_font_size
        self.on_zoom_changed = on_zoom_changed

        self.current_zoom = self.DEFAULT_ZOOM
        self._delta_accumulator = 0.0
        self.zoom_var = tk.StringVar(value=f"{self.current_zoom}%")

    def set_zoom(self, percent: int) -> None:
        """Set zoom percentage (clamped to [MIN_ZOOM, MAX_ZOOM]) and update font."""
        clamped = max(self.MIN_ZOOM, min(self.MAX_ZOOM, int(percent)))
        self.current_zoom = clamped
        self.zoom_var.set(f"{self.current_zoom}%")

        # Compute new font size
        new_size = max(5, int(round(self.base_font_size * (self.current_zoom / 100.0))))

        try:
            # Preserve current visible top line
            first_visible = self.text_widget.index("@0,0")
        except Exception:
            first_visible = "1.0"

        # Apply new font size
        self.text_widget.configure(font=(self.base_font_family, new_size))

        try:
            # Re-anchor to the previously visible line so it does not jump to top
            self.text_widget.see(first_visible)
        except Exception:
            pass

        if self.on_zoom_changed:
            self.on_zoom_changed(self.current_zoom)

    def zoom_in(self) -> None:
        """Increase zoom by ZOOM_STEP."""
        if self.current_zoom < self.MAX_ZOOM:
            self.set_zoom(self.current_zoom + self.ZOOM_STEP)

    def zoom_out(self) -> None:
        """Decrease zoom by ZOOM_STEP."""
        if self.current_zoom > self.MIN_ZOOM:
            self.set_zoom(self.current_zoom - self.ZOOM_STEP)

    def zoom_reset(self) -> None:
        """Reset zoom to DEFAULT_ZOOM (100%)."""
        self.set_zoom(self.DEFAULT_ZOOM)

    def attach_toolbar_controls(self, parent_frame: ttk.Frame) -> ttk.Frame:
        """Create and pack [-], [100%], [+], [Reset] controls into a sub-frame."""
        frame = ttk.Frame(parent_frame)

        ttk.Label(frame, text="Zoom:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 2))
        btn_out = ttk.Button(frame, text="−", width=3, command=self.zoom_out)
        btn_out.pack(side=tk.LEFT, padx=1)

        lbl_val = ttk.Label(
            frame,
            textvariable=self.zoom_var,
            width=5,
            anchor=tk.CENTER,
            font=("Segoe UI", 9, "bold"),
        )
        lbl_val.pack(side=tk.LEFT, padx=2)

        btn_in = ttk.Button(frame, text="+", width=3, command=self.zoom_in)
        btn_in.pack(side=tk.LEFT, padx=1)

        btn_reset = ttk.Button(frame, text="Reset", width=5, command=self.zoom_reset)
        btn_reset.pack(side=tk.LEFT, padx=(2, 4))

        return frame

    def bind_events(self, root_window: tk.Misc) -> None:
        """Bind keyboard shortcuts and wheel/touchpad gestures."""
        # Keyboard zoom controls
        root_window.bind("<Control-plus>", lambda _e: (self.zoom_in(), "break")[1])
        root_window.bind("<Control-equal>", lambda _e: (self.zoom_in(), "break")[1])
        root_window.bind("<Control-KP_Add>", lambda _e: (self.zoom_in(), "break")[1])

        root_window.bind("<Control-minus>", lambda _e: (self.zoom_out(), "break")[1])
        root_window.bind("<Control-underscore>", lambda _e: (self.zoom_out(), "break")[1])
        root_window.bind("<Control-KP_Subtract>", lambda _e: (self.zoom_out(), "break")[1])

        root_window.bind("<Control-Key-0>", lambda _e: (self.zoom_reset(), "break")[1])
        root_window.bind("<Control-KP_0>", lambda _e: (self.zoom_reset(), "break")[1])

        # Mouse wheel & precision touchpad pinch on text widget
        # When Control key is held, MouseWheel triggers zoom and stops outer scrolling
        self.text_widget.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)

    def _on_ctrl_mousewheel(self, event: tk.Event) -> str:
        """
        Handle Ctrl + MouseWheel / Touchpad Pinch.
        Accumulates delta so high-resolution precision touchpads are smooth, not jumpy.
        Returns 'break' to prevent event bubbling to parent containers/canvases.
        """
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"

        self._delta_accumulator += delta
        # Threshold: 60 delta units (~half a notch or multiple micro-deltas)
        threshold = 60.0
        if abs(self._delta_accumulator) >= threshold:
            steps = int(self._delta_accumulator / threshold)
            self._delta_accumulator -= steps * threshold
            if steps > 0:
                for _ in range(steps):
                    self.zoom_in()
            else:
                for _ in range(-steps):
                    self.zoom_out()

        return "break"

