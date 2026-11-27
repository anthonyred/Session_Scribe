"""
gui/scrollable_tab.py

Reusable scrollable container for Session Scribe tabs.

Usage
-----
    class MyTab(ScrollableTab):
        def __init__(self, parent, ...):
            super().__init__(parent)
            # put all content into self.inner, NOT self
            ttk.Label(self.inner, text="Hello").pack()
            ...

ScrollableTab wraps its *inner* frame in a tk.Canvas + vertical ttk.Scrollbar.
A horizontal scrollbar is added only when the content would overflow the canvas
width at the current window size.

Scrolling behaviour
-------------------
* Application-level wheel handler: routes by the widget under the pointer at all times.
* When the pointer is over an *inner scrollable widget* (Treeview, Text,
  ScrolledText, Listbox, Spinbox), wheel events are delivered to that widget
  first. The tab scrolls only when the inner widget has reached its top or
  bottom end.
* Over all other tab widgets (buttons, labels, frames, empty areas), the mouse wheel
  and touchpad gestures immediately scroll the tab.
* Works seamlessly after any modal dialog closes and across tab switches.
* Keyboard users: Tab-key focus traversal on interactive inputs scrolls to keep
  the focused widget in view, without disrupting layout or scroll position
  during dialog open/close or mouse clicks.

Thread safety
-------------
All Tk calls happen on the main thread; this module is UI-only.
"""

from __future__ import annotations

from contextlib import contextmanager
import tkinter as tk
from tkinter import ttk
from . import theme

# Widget classes whose own vertical scroll should take priority over the tab scroll.
_INNER_SCROLL_CLASSES = (
    "Treeview",
    "Text",        # covers ScrolledText (which embeds a Text)
    "Listbox",
    "Spinbox",
    "TSpinbox",
)

# Container widget classes that should never trigger automatic scroll-into-view
_CONTAINER_CLASSES = (
    "Canvas",
    "Frame",
    "TFrame",
    "Labelframe",
    "TLabelframe",
    "Panedwindow",
    "TPanedwindow",
    "Notebook",
    "TNotebook",
    "Tk",
    "Toplevel",
    "Button",
    "TButton",
)

_APPLICATION_WHEEL_INSTALLED = False


def _app_route_wheel(event: tk.Event) -> str | None:
    """
    Application-level mouse-wheel routing:
    Inspects the widget directly under the pointer. If over an inner scrollable
    control (Treeview, Text, Listbox, Spinbox), lets that control scroll first.
    If at the control's boundary, or if hovering over any other tab widget
    (buttons, entries, frames, background), scrolls the active ScrollableTab canvas.
    """
    x = getattr(event, "x_root", None)
    y = getattr(event, "y_root", None)

    target = None
    if x is not None and y is not None:
        try:
            target = event.widget.winfo_containing(x, y)
        except Exception:
            target = None

    if target is None:
        target = getattr(event, "widget", None)

    if target is None:
        return None

    # Walk up the widget hierarchy to find the containing ScrollableTab,
    # any active modal/dialog UniversalScroller, and any inner scrollable widget under the pointer.
    cur = target
    found_tab: ScrollableTab | None = None
    found_dialog_scroller: UniversalScroller | None = None
    inner_scrollable: tk.Widget | None = None

    while cur is not None:
        cls_name = ""
        try:
            cls_name = cur.winfo_class()
        except Exception:
            pass

        if inner_scrollable is None and (
            cls_name in _INNER_SCROLL_CLASSES
            or isinstance(cur, (ttk.Treeview, tk.Text, tk.Listbox, ttk.Spinbox, tk.Spinbox))
        ):
            inner_scrollable = cur

        if found_dialog_scroller is None and getattr(cur, "_universal_scroller", None) is not None:
            found_dialog_scroller = getattr(cur, "_universal_scroller", None)

        if isinstance(cur, ScrollableTab):
            found_tab = cur
            break

        try:
            cur = cur.master
        except (AttributeError, KeyError):
            break

    delta = _wheel_delta(event)
    raw_delta = getattr(event, "delta", 0)
    if delta == 0 and not raw_delta:
        return None

    # 1. Inner scrollable widget priority (if not at top/bottom boundary)
    if inner_scrollable is not None:
        try:
            lo, hi = inner_scrollable.yview()
        except Exception:
            lo, hi = 0.0, 1.0

        at_top = lo <= 0.0
        at_bottom = hi >= 1.0

        eff_delta = delta if delta != 0 else (1 if raw_delta > 0 else -1)
        if eff_delta > 0 and not at_top:
            inner_scrollable.yview_scroll(-eff_delta, "units")
            return "break"
        elif eff_delta < 0 and not at_bottom:
            inner_scrollable.yview_scroll(-eff_delta, "units")
            return "break"

    # 2. Scroll dialog canvas if cursor is inside a dialog with an attached UniversalScroller
    if found_dialog_scroller is not None:
        return found_dialog_scroller.handle_wheel_event(event)

    # 3. Scroll the containing tab
    if found_tab is not None:
        if hasattr(found_tab, "_scroller") and found_tab._scroller is not None:
            return found_tab._scroller.handle_wheel_event(event)
        found_tab._scroll_tab(delta)
        return "break"

    return None


class UniversalScroller:
    """
    Manages smooth mouse-wheel delta accumulation, Shift+wheel horizontal scrolling,
    keyboard navigation, and drag-to-scroll gesture pan for a tk.Canvas.
    """
    def __init__(
        self,
        canvas: tk.Canvas,
        content_widget: Optional[tk.Widget] = None,
        toplevel: Optional[tk.Toplevel] = None,
        enable_drag_pan: bool = True,
        drag_threshold: int = 6,
    ):
        self.canvas = canvas
        self.content_widget = content_widget
        self.toplevel = toplevel
        self.enable_drag_pan = enable_drag_pan
        self.drag_threshold = drag_threshold

        self.delta_y_acc: float = 0.0
        self.delta_x_acc: float = 0.0

        self._drag_start_y: float = 0.0
        self._drag_start_x: float = 0.0
        self._drag_start_yview: float = 0.0
        self._is_dragging: bool = False

        if toplevel is not None:
            toplevel._universal_scroller = self
            # Keyboard navigation on dialog
            toplevel.bind("<Prior>", self._on_page_up, add="+")
            toplevel.bind("<Next>", self._on_page_down, add="+")
            toplevel.bind("<Home>", self._on_home, add="+")
            toplevel.bind("<End>", self._on_end, add="+")
            # Dialog-level wheel binding to ensure dialog events don't get swallowed or leak
            toplevel.bind("<MouseWheel>", self.handle_wheel_event, add="+")
            toplevel.bind("<Button-4>", self.handle_wheel_event, add="+")
            toplevel.bind("<Button-5>", self.handle_wheel_event, add="+")

        canvas._universal_scroller = self
        if content_widget is not None:
            content_widget._universal_scroller = self

        if self.enable_drag_pan:
            self._setup_drag_pan()

    def handle_wheel_event(self, event: tk.Event) -> Optional[str]:
        """
        Handle mouse wheel with sub-notch delta accumulation and Shift-wheel horizontal scrolling.
        Never divides by zero.
        """
        num = getattr(event, "num", None)
        if num == 4:
            self.canvas.yview_scroll(-1, "units")
            return "break"
        if num == 5:
            self.canvas.yview_scroll(1, "units")
            return "break"

        raw_delta = getattr(event, "delta", 0)
        if not raw_delta:
            return None

        # Check Shift key modifier for horizontal scroll (state & 0x0001)
        state = getattr(event, "state", 0)
        is_shift = bool(state & 0x0001)

        if is_shift:
            self.delta_x_acc += raw_delta
            if abs(self.delta_x_acc) >= 30.0:
                steps = int(self.delta_x_acc / 30.0)
                self.delta_x_acc -= steps * 30.0
                try:
                    self.canvas.xview_scroll(-steps, "units")
                except Exception:
                    pass
                return "break"
        else:
            self.delta_y_acc += raw_delta
            if abs(self.delta_y_acc) >= 30.0:
                steps = int(self.delta_y_acc / 30.0)
                self.delta_y_acc -= steps * 30.0
                try:
                    self.canvas.yview_scroll(-steps, "units")
                except Exception:
                    pass
                return "break"

        return "break"

    def _on_page_up(self, _e=None):
        self.canvas.yview_scroll(-1, "pages")
        return "break"

    def _on_page_down(self, _e=None):
        self.canvas.yview_scroll(1, "pages")
        return "break"

    def _on_home(self, _e=None):
        self.canvas.yview_moveto(0.0)
        return "break"

    def _on_end(self, _e=None):
        self.canvas.yview_moveto(1.0)
        return "break"

    def _setup_drag_pan(self):
        targets = [self.canvas]
        if self.content_widget is not None:
            targets.append(self.content_widget)

        for w in targets:
            w.bind("<ButtonPress-1>", self._on_drag_press, add="+")
            w.bind("<B1-Motion>", self._on_drag_motion, add="+")
            w.bind("<ButtonRelease-1>", self._on_drag_release, add="+")

    def register_drag_widget(self, widget: tk.Widget):
        """Allow child container frames or labels to participate in drag-to-scroll."""
        widget._universal_scroller = self
        if self.enable_drag_pan:
            widget.bind("<ButtonPress-1>", self._on_drag_press, add="+")
            widget.bind("<B1-Motion>", self._on_drag_motion, add="+")
            widget.bind("<ButtonRelease-1>", self._on_drag_release, add="+")

    def _on_drag_press(self, event: tk.Event):
        self._drag_start_y = event.y_root
        self._drag_start_x = event.x_root
        try:
            self._drag_start_yview = self.canvas.yview()[0]
        except Exception:
            self._drag_start_yview = 0.0
        self._is_dragging = False

    def _on_drag_motion(self, event: tk.Event):
        dy = event.y_root - self._drag_start_y
        if not self._is_dragging:
            if abs(dy) >= self.drag_threshold:
                self._is_dragging = True

        if self._is_dragging:
            try:
                bbox = self.canvas.bbox("all")
                total_h = (bbox[3] - bbox[1]) if bbox else self.canvas.winfo_height()
                if total_h > 0:
                    fraction = dy / float(total_h)
                    new_pos = max(0.0, min(1.0, self._drag_start_yview - fraction))
                    self.canvas.yview_moveto(new_pos)
            except Exception:
                pass
            return "break"

    def _on_drag_release(self, event: tk.Event):
        if self._is_dragging:
            self._is_dragging = False
            return "break"


def attach_universal_scrolling(
    canvas: tk.Canvas,
    content_widget: Optional[tk.Widget] = None,
    toplevel: Optional[tk.Toplevel] = None,
    enable_drag_pan: bool = True,
    drag_threshold: int = 6,
) -> UniversalScroller:
    """Helper to attach smooth wheel accumulation, keyboard nav, and touch/mouse drag-to-scroll."""
    return UniversalScroller(
        canvas=canvas,
        content_widget=content_widget,
        toplevel=toplevel,
        enable_drag_pan=enable_drag_pan,
        drag_threshold=drag_threshold,
    )


def install_application_wheel_handler(root: tk.Misc) -> None:
    """Installs the unified application-level wheel handler once."""
    global _APPLICATION_WHEEL_INSTALLED
    if _APPLICATION_WHEEL_INSTALLED:
        return
    _APPLICATION_WHEEL_INSTALLED = True

    try:
        root.bind_all("<MouseWheel>", _app_route_wheel, add="+")
        root.bind_all("<Button-4>",   _app_route_wheel, add="+")
        root.bind_all("<Button-5>",   _app_route_wheel, add="+")
    except Exception:
        pass


class ScrollableTab(ttk.Frame):
    """
    A tab-level frame whose content sits inside a scrollable canvas.

    Place all child widgets inside ``self.inner``, not ``self`` directly.
    """

    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, **kwargs)

        self._suppress_focus_scroll = False

        # ---- Scrollbars ----
        self._vbar = ttk.Scrollbar(self, orient=tk.VERTICAL)
        self._hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL)

        # ---- Canvas ----
        self._canvas = tk.Canvas(
            self,
            highlightthickness=0,
            bg=theme.COLOR_APP_BG,
            yscrollcommand=self._vbar.set,
            xscrollcommand=self._hbar.set,
        )
        self._vbar.configure(command=self._canvas.yview)
        self._hbar.configure(command=self._canvas.xview)

        # Grid layout: canvas fills the cell, scrollbars on edges.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar.grid(row=0, column=1, sticky="ns")
        # Horizontal bar added lazily in _on_inner_configure when needed.
        self._hbar_visible = False

        # ---- Inner frame (the actual content holder) ----
        self.inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )

        # ---- Events ----
        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Wheel on canvas itself as fallback
        self._canvas.bind("<MouseWheel>", self._on_canvas_wheel, add="+")
        self._canvas.bind("<Button-4>",   self._on_canvas_wheel, add="+")
        self._canvas.bind("<Button-5>",   self._on_canvas_wheel, add="+")

        # Attach universal scroller for delta accumulation and touch/drag pan
        self._scroller = UniversalScroller(self._canvas, self.inner, enable_drag_pan=True)

        # Install application-level mouse-wheel routing across all widgets
        install_application_wheel_handler(self)

        # Track focus changes to scroll focused widget into view when using Tab traversal.
        self.bind_all("<FocusIn>", self._on_focus_in, add="+")

    def refresh_theme(self) -> None:
        """Update canvas background to match active theme."""
        if hasattr(self, "_canvas"):
            self._canvas.configure(bg=theme.COLOR_APP_BG)

    # ------------------------------------------------------------------
    # Context manager to preserve scroll position
    # ------------------------------------------------------------------

    @contextmanager
    def preserve_scroll(self):
        """
        Context manager ensuring the canvas scroll position remains unchanged
        across modal dialogs, folder browse popups, scans, and rebuild actions.
        """
        saved_lo = self._canvas.yview()[0]
        self._suppress_focus_scroll = True
        try:
            yield
        finally:
            self._canvas.yview_moveto(saved_lo)
            self._suppress_focus_scroll = False

    # ------------------------------------------------------------------
    # Internal geometry management
    # ------------------------------------------------------------------

    def _on_inner_configure(self, _event=None):
        """Update scroll region height when inner content expands/contracts."""
        canvas_w = self._canvas.winfo_width()
        canvas_h = self._canvas.winfo_height()
        req_h = self.inner.winfo_reqheight()
        self._canvas.configure(scrollregion=(0, 0, max(1, canvas_w), max(req_h, canvas_h)))
        self._update_hbar()

    def _on_canvas_configure(self, event):
        """Fit inner frame strictly to canvas width so content scales with window."""
        self._canvas.itemconfigure(self._inner_id, width=event.width)
        req_h = self.inner.winfo_reqheight()
        self._canvas.configure(scrollregion=(0, 0, max(1, event.width), max(req_h, event.height)))
        self._update_hbar()

    def _update_hbar(self):
        """Show the horizontal scrollbar only when content is wider than canvas."""
        canvas_w = self._canvas.winfo_width()
        inner_w  = self.inner.winfo_reqwidth()
        needs_hbar = inner_w > canvas_w > 0
        if needs_hbar and not self._hbar_visible:
            self._hbar.grid(row=1, column=0, sticky="ew")
            self._hbar_visible = True
        elif not needs_hbar and self._hbar_visible:
            self._hbar.grid_remove()
            self._hbar_visible = False

    # ------------------------------------------------------------------
    # Mouse-wheel routing
    # ------------------------------------------------------------------

    def register_wheel(self, widget: tk.Widget):
        """
        Retained for backward compatibility. The unified application-level
        handler already routes wheel gestures over all widgets automatically.
        """
        pass

    def _on_canvas_wheel(self, event: tk.Event):
        """Scroll the tab when the wheel fires directly on the canvas."""
        self._scroll_tab(_wheel_delta(event))
        return "break"

    def _scroll_tab(self, delta: int):
        """Scroll the canvas by *delta* units (positive = up, negative = down)."""
        self._canvas.yview_scroll(-delta, "units")

    # ------------------------------------------------------------------
    # Keyboard focus tracking
    # ------------------------------------------------------------------

    def _on_focus_in(self, event: tk.Event):
        """Scroll the canvas so that the newly focused widget is in view."""
        if self._suppress_focus_scroll:
            return

        widget = event.widget
        if widget is self or widget is self._canvas or widget is self.inner:
            return

        try:
            cls_name = widget.winfo_class()
            if cls_name in _CONTAINER_CLASSES:
                return
            if not self._is_descendant(widget):
                return
        except Exception:
            return

        self.after(10, lambda w=widget: self._scroll_widget_into_view(w))

    def _is_descendant(self, widget: tk.Widget) -> bool:
        w = widget
        for _ in range(50):
            if w is self.inner:
                return True
            try:
                w = w.master
            except AttributeError:
                break
            if w is None:
                break
        return False

    def _scroll_widget_into_view(self, widget: tk.Widget):
        """Adjust the canvas yview so *widget* is fully visible if not already in view."""
        if self._suppress_focus_scroll:
            return

        try:
            if not widget.winfo_exists():
                return
            canvas_h = self._canvas.winfo_height()
            if canvas_h <= 1:
                return

            wy = widget.winfo_rooty() - self._canvas.winfo_rooty()
            wh = widget.winfo_height()

            # If widget is already fully visible in the canvas viewport, do nothing
            if wy >= 0 and (wy + wh) <= canvas_h:
                return

            lo, _hi = self._canvas.yview()
            total_h = self.inner.winfo_reqheight()
            if total_h <= 0:
                return
            scroll_y = lo * total_h

            widget_top    = wy + scroll_y
            widget_bottom = widget_top + wh

            if wy < 0:
                # Widget is above visible area — scroll up so top of widget aligns with canvas top
                self._canvas.yview_moveto(max(0.0, widget_top / total_h))
            elif wy + wh > canvas_h:
                # Widget is below visible area
                if wh >= canvas_h:
                    # Widget larger than viewport — align top
                    self._canvas.yview_moveto(max(0.0, widget_top / total_h))
                else:
                    new_top = widget_bottom - canvas_h
                    self._canvas.yview_moveto(max(0.0, new_top / total_h))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _wheel_delta(event: tk.Event) -> int:
    """
    Return +1 (scroll up / towards content start) or -1 (scroll down).
    Handles Windows (<MouseWheel> event.delta in multiples of 120),
    Linux (Button-4 = up, Button-5 = down), and macOS trackpads.
    """
    num = getattr(event, "num", None)
    if num == 4:
        return 1
    if num == 5:
        return -1
    delta = getattr(event, "delta", 0)
    if delta:
        units = int(delta / 120)
        return units if units != 0 else (1 if delta > 0 else -1)
    return 0
