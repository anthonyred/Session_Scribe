"""
tests/test_scrolling_and_shortcuts.py

Verification tests for:
  Bug A — Tabs scrollable; tables not squashed at small window sizes
  Bug B — Ctrl word-editing shortcuts work correctly, no stray chars

Rules (AGENTS.md §4):
  - No real event data touched.
  - All I/O uses temp directories.
  - sys.path guard derived from __file__.

Notes on test approach for Bug B
---------------------------------
  event_generate() only fires class-level bindings when the widget has keyboard
  focus.  To avoid a race with the window manager we test two ways:
    1. Pure unit tests on the pure-Python helper functions (_prev_word_start, etc.)
       — these cover correctness of word-boundary logic without Tk.
    2. Integration tests that call the handler functions directly (bypassing
       event dispatch) and then assert widget state.
  The direct-call approach mirrors exactly what the production code does and gives
  reliable results on headless / hidden Tk roots.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

# ---- sys.path guard ---------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest

from gui.text_shortcuts import (
    _prev_word_start,
    _next_word_end,
    apply_text_shortcuts,
    _entry_ctrl_backspace,
    _entry_ctrl_a,
    _text_ctrl_backspace,
    _text_ctrl_a,
)
from gui.scrollable_tab import ScrollableTab, _wheel_delta


# -----------------------------------------------------------------------
# Fixture: a hidden Tk root for all widget tests
# -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def root():
    """Create a single Tk root, withdraw it, yield it, then destroy it."""
    r = tk.Tk()
    r.withdraw()
    apply_text_shortcuts(r)
    yield r
    try:
        r.destroy()
    except Exception:
        pass


# -----------------------------------------------------------------------
# Helper: build a fake event object pointing at a widget
# -----------------------------------------------------------------------

class FakeEvent:
    def __init__(self, widget):
        self.widget = widget


# -----------------------------------------------------------------------
# Bug B — word-boundary navigation helpers (pure Python, no Tk needed)
# -----------------------------------------------------------------------

class TestWordBoundary:

    # _prev_word_start ---------------------------------------------------

    def test_prev_word_at_middle(self):
        text = "hello world"
        assert _prev_word_start(text, 11) == 6   # start of 'world'

    def test_prev_word_skips_punctuation(self):
        # "VID001.mp4" cursor at end → jumps to 'm' (start of 'mp4')
        text = "VID001.mp4"
        assert _prev_word_start(text, 10) == 7

    def test_prev_word_from_word_start_jumps_over_punct_to_prev_word(self):
        # "abc.def" cursor at 4 (start of 'def') → jumps to start of 'abc'
        text = "abc.def"
        assert _prev_word_start(text, 4) == 0

    def test_prev_word_at_start_returns_zero(self):
        assert _prev_word_start("hello", 0) == 0

    def test_prev_word_underscore_boundary(self):
        # "first_second" → 'second' starts at index 6
        text = "first_second"
        assert _prev_word_start(text, 12) == 6

    def test_prev_word_comma_boundary(self):
        # "VID001, VID002" cursor at 14 → start of 'VID002' = 8
        text = "VID001, VID002"
        result = _prev_word_start(text, 14)
        assert result == 8

    def test_prev_word_hyphen_boundary(self):
        text = "Kalyani-Patra"
        # cursor at end (13) → start of 'Patra' = 8
        assert _prev_word_start(text, 13) == 8

    # _next_word_end -----------------------------------------------------

    def test_next_word_from_start(self):
        text = "hello world"
        assert _next_word_end(text, 0) == 5

    def test_next_word_skips_leading_punct(self):
        # "VID001.mp4" from index 6 (after 'VID001') → end of 'mp4' = 10
        text = "VID001.mp4"
        assert _next_word_end(text, 6) == 10

    def test_next_word_at_end_returns_length(self):
        text = "hello"
        assert _next_word_end(text, 5) == 5

    def test_next_word_comma_boundary(self):
        text = "VID001, VID002"
        assert _next_word_end(text, 0) == 6

    def test_next_word_hyphen_boundary(self):
        text = "Kalyani-Patra"
        assert _next_word_end(text, 0) == 7

    # Symmetry check
    def test_round_trip_words(self):
        """Going right by words then left should return to start."""
        text = "VID001.mp4, VID002.mp4 - Kalyani Patra"
        pos = 0
        positions = [pos]
        while pos < len(text):
            pos = _next_word_end(text, pos)
            positions.append(pos)
        back = positions[-1]
        reversed_positions = [back]
        while back > 0:
            back = _prev_word_start(text, back)
            reversed_positions.append(back)
        assert reversed_positions[-1] == 0


# -----------------------------------------------------------------------
# Bug B — handler integration tests (call handler directly, no focus needed)
# -----------------------------------------------------------------------

class TestCtrlHandlerIntegration:
    """
    Call the handler functions directly with a fake event.
    This is equivalent to what Tk does when the binding fires.
    """

    # --- Entry ---

    def test_entry_ctrl_backspace_deletes_word(self, root):
        e = tk.Entry(root)
        e.insert(0, "hello world")
        e.icursor(tk.END)
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert e.get() == "hello "

    def test_entry_ctrl_backspace_no_stray_char(self, root):
        e = tk.Entry(root)
        e.insert(0, "abc")
        e.icursor(tk.END)
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        content = e.get()
        assert all(ord(c) >= 32 for c in content), f"Stray control char in: {content!r}"

    def test_entry_ctrl_backspace_with_selection_deletes_sel(self, root):
        e = tk.Entry(root)
        e.insert(0, "hello world")
        e.selection_range(6, 11)
        e.icursor(11)
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert e.get() == "hello "

    def test_entry_ctrl_backspace_multiple_words(self, root):
        """Three consecutive Ctrl+Backspace calls delete three words."""
        e = tk.Entry(root)
        e.insert(0, "alpha beta gamma")
        e.icursor(tk.END)
        for _ in range(3):
            _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        # After 3 word-deletions: "alpha beta gamma" → "alpha beta " → "alpha " → ""
        assert e.get() == ""

    def test_entry_ctrl_a_selects_all(self, root):
        e = tk.Entry(root)
        e.insert(0, "Select me")
        _entry_ctrl_a(FakeEvent(e))
        root.update_idletasks()
        # cursor should be at end
        assert e.index(tk.INSERT) == len("Select me")

    def test_readonly_entry_not_modified(self, root):
        var = tk.StringVar(value="protected")
        e = tk.Entry(root, textvariable=var, state="readonly")
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert var.get() == "protected"

    def test_disabled_entry_not_modified(self, root):
        var = tk.StringVar(value="protected")
        e = tk.Entry(root, textvariable=var, state="disabled")
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert var.get() == "protected"

    def test_entry_filename_word_deletion(self, root):
        """Simulates user pressing Ctrl+Backspace after typing a filename."""
        e = tk.Entry(root)
        e.insert(0, "VID001.mp4")
        e.icursor(tk.END)
        # First press: deletes 'mp4'
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert e.get() == "VID001."
        # Second press: deletes 'VID001'
        _entry_ctrl_backspace(FakeEvent(e))
        root.update_idletasks()
        assert e.get() == ""

    # --- Text ---

    def test_text_ctrl_backspace_deletes_word(self, root):
        t = tk.Text(root, height=2)
        t.insert("1.0", "hello world")
        t.mark_set(tk.INSERT, tk.END)
        _text_ctrl_backspace(FakeEvent(t))
        root.update_idletasks()
        content = t.get("1.0", tk.END).rstrip("\n")
        assert content == "hello "

    def test_text_ctrl_backspace_no_stray_char(self, root):
        t = tk.Text(root, height=2)
        t.insert("1.0", "abc")
        t.mark_set(tk.INSERT, tk.END)
        _text_ctrl_backspace(FakeEvent(t))
        root.update_idletasks()
        content = t.get("1.0", tk.END).rstrip("\n")
        assert all(ord(c) >= 32 for c in content), f"Stray: {content!r}"

    def test_text_ctrl_a_selects_all(self, root):
        t = tk.Text(root, height=2)
        t.insert("1.0", "hello world")
        _text_ctrl_a(FakeEvent(t))
        root.update_idletasks()
        sel_ranges = t.tag_ranges(tk.SEL)
        assert len(sel_ranges) == 2

    def test_mapping_box_ctrl_backspace_filename(self, root):
        """
        Simulate pasting a filename list and deleting the last filename
        word-by-word with Ctrl+Backspace.
        """
        t = tk.Text(root, height=5)
        content = "VID001.mp4, VID002.mp4"
        t.insert("1.0", content)
        t.mark_set(tk.INSERT, tk.END)

        # First: deletes 'mp4'
        _text_ctrl_backspace(FakeEvent(t))
        root.update_idletasks()
        assert t.get("1.0", tk.END).rstrip("\n") == "VID001.mp4, VID002."

        # Second: deletes 'VID002'
        _text_ctrl_backspace(FakeEvent(t))
        root.update_idletasks()
        assert t.get("1.0", tk.END).rstrip("\n") == "VID001.mp4, "

    def test_text_ctrl_backspace_with_selection(self, root):
        """If text is selected, Ctrl+Backspace deletes the selection."""
        t = tk.Text(root, height=2)
        t.insert("1.0", "hello world")
        t.tag_add(tk.SEL, "1.6", "1.11")  # select 'world'
        t.mark_set(tk.INSERT, "1.11")
        _text_ctrl_backspace(FakeEvent(t))
        root.update_idletasks()
        content = t.get("1.0", tk.END).rstrip("\n")
        assert content == "hello "


# -----------------------------------------------------------------------
# Bug A — ScrollableTab geometry (headless)
# -----------------------------------------------------------------------

class TestScrollableTab:

    def test_inner_frame_exists(self, root):
        tab = ScrollableTab(root)
        assert hasattr(tab, "inner")
        assert isinstance(tab.inner, ttk.Frame)

    def test_canvas_and_vbar_present(self, root):
        tab = ScrollableTab(root)
        assert hasattr(tab, "_canvas")
        assert hasattr(tab, "_vbar")

    def test_content_inside_inner_not_squashed(self, root):
        """
        Pack a tall label into inner; the canvas scrollregion must be at
        least as tall as the label's requested height.
        """
        tab = ScrollableTab(root)
        tab.pack(fill=tk.BOTH, expand=True)
        root.update_idletasks()

        lbl = ttk.Label(tab.inner, text="\n".join(["Row"] * 20))
        lbl.pack()
        root.update_idletasks()

        req_h = tab.inner.winfo_reqheight()
        bbox = tab._canvas.bbox("all")
        if bbox:
            _, _, _, sr_h = bbox
            assert sr_h >= req_h - 2

    def test_register_wheel_does_not_raise(self, root):
        tab = ScrollableTab(root)
        tree = ttk.Treeview(tab.inner, columns=("a",), show="headings", height=4)
        tree.pack()
        tab.register_wheel(tree)   # must not raise

    def test_treeview_height_respected(self, root):
        """
        A Treeview with height=8 inside ScrollableTab must have a requested
        height >= 8 * some positive row height (≥ 8 pixels per row minimum).
        """
        tab = ScrollableTab(root)
        tab.pack()
        tree = ttk.Treeview(tab.inner, columns=("a",), show="headings", height=8)
        tree.pack(fill=tk.X)
        root.update_idletasks()

        req_h = tree.winfo_reqheight()
        # 8 rows × at least 10px each = 80px minimum
        assert req_h >= 80, f"Treeview reqheight only {req_h}px for height=8"

    def test_multiple_sections_accumulate_height(self, root):
        """
        Two LabelFrames packed into inner must produce a combined inner
        reqheight greater than either one alone.
        """
        tab = ScrollableTab(root)
        tab.pack()
        f1 = ttk.LabelFrame(tab.inner, text="Section 1", padding=10)
        f1.pack(fill=tk.X)
        ttk.Label(f1, text="Content A").pack()

        f2 = ttk.LabelFrame(tab.inner, text="Section 2", padding=10)
        f2.pack(fill=tk.X)
        ttk.Label(f2, text="Content B").pack()

        root.update_idletasks()
        combined_h = tab.inner.winfo_reqheight()
        h1 = f1.winfo_reqheight()
        h2 = f2.winfo_reqheight()
        assert combined_h >= h1 + h2 - 4  # small rounding tolerance


# -----------------------------------------------------------------------
# Bug B — _wheel_delta utility
# -----------------------------------------------------------------------

class TestWheelDelta:

    def _make_event(self, num=0, delta=0):
        class FakeWheelEvent:
            pass
        e = FakeWheelEvent()
        e.num   = num
        e.delta = delta
        return e

    def test_button4_is_up(self):
        assert _wheel_delta(self._make_event(num=4)) == 1

    def test_button5_is_down(self):
        assert _wheel_delta(self._make_event(num=5)) == -1

    def test_positive_delta_is_up(self):
        assert _wheel_delta(self._make_event(delta=120)) == 1

    def test_negative_delta_is_down(self):
        assert _wheel_delta(self._make_event(delta=-120)) == -1


# -----------------------------------------------------------------------
# Part 2: Universal Scroller (Touchpad sub-notches, Shift-wheel, Drag Pan)
# -----------------------------------------------------------------------

class TestUniversalScroller:

    def test_touchpad_delta_accumulation(self, root):
        from gui.scrollable_tab import UniversalScroller
        canvas = tk.Canvas(root, height=300)
        scroller = UniversalScroller(canvas, enable_drag_pan=False)

        class FakeWheelEvent:
            def __init__(self, delta, state=0):
                self.delta = delta
                self.state = state
                self.num = 0

        # Small 10-unit notch: accumulated, no scroll yet (threshold 30)
        ev1 = FakeWheelEvent(delta=10)
        res1 = scroller.handle_wheel_event(ev1)
        assert res1 == "break"
        assert scroller.delta_y_acc == 10.0

        # Another 10 units -> 20.0
        scroller.handle_wheel_event(FakeWheelEvent(delta=10))
        assert scroller.delta_y_acc == 20.0

        # Another 15 units -> total 35.0, crosses 30.0 threshold!
        # Steps = int(35 / 30) = 1, remaining accumulator = 5.0
        scroller.handle_wheel_event(FakeWheelEvent(delta=15))
        assert scroller.delta_y_acc == 5.0

    def test_zero_delta_never_divides_by_zero(self, root):
        from gui.scrollable_tab import UniversalScroller
        canvas = tk.Canvas(root, height=300)
        scroller = UniversalScroller(canvas)

        class FakeWheelEvent:
            delta = 0
            state = 0
            num = 0

        res = scroller.handle_wheel_event(FakeWheelEvent())
        assert res is None
        assert scroller.delta_y_acc == 0.0

    def test_shift_wheel_horizontal_scroll(self, root):
        from gui.scrollable_tab import UniversalScroller
        canvas = tk.Canvas(root, width=300, height=300)
        scroller = UniversalScroller(canvas)

        class FakeWheelEvent:
            def __init__(self, delta, state):
                self.delta = delta
                self.state = state
                self.num = 0

        # Shift key pressed (state & 0x0001)
        ev = FakeWheelEvent(delta=-60, state=1)
        res = scroller.handle_wheel_event(ev)
        assert res == "break"
        # delta_x_acc accumulated and stepped
        assert scroller.delta_x_acc == 0.0

    def test_drag_to_scroll_gesture_threshold(self, root):
        from gui.scrollable_tab import UniversalScroller
        canvas = tk.Canvas(root, height=300)
        canvas.configure(scrollregion=(0, 0, 300, 1000))
        scroller = UniversalScroller(canvas, enable_drag_pan=True, drag_threshold=6)

        class FakeMouseEvent:
            def __init__(self, x_root, y_root):
                self.x_root = x_root
                self.y_root = y_root

        # 1. Press at y=100
        scroller._on_drag_press(FakeMouseEvent(100, 100))
        assert scroller._is_dragging is False
        assert scroller._drag_start_y == 100

        # 2. Small movement (3px) -> below threshold (6px), no drag
        scroller._on_drag_motion(FakeMouseEvent(100, 97))
        assert scroller._is_dragging is False

        # 3. Movement past threshold (20px) -> activates dragging
        res = scroller._on_drag_motion(FakeMouseEvent(100, 80))
        assert scroller._is_dragging is True
        assert res == "break"

        # 4. Release -> resets drag state and swallows event
        res_rel = scroller._on_drag_release(FakeMouseEvent(100, 80))
        assert scroller._is_dragging is False
        assert res_rel == "break"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
