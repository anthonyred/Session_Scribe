"""Shared event-setup state for Stage 2 and Stage 3. Nothing is persisted between launches."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, List


class EventContext:
    """One event-folder source and the Stage 2 form fields, shared by both tabs."""

    def __init__(self):
        self.event_dir_var = tk.StringVar(value="")
        self.event_name_var = tk.StringVar(value="")
        self.event_date_var = tk.StringVar(value="")
        self.organiser_var = tk.StringVar(value="")
        self.venue_var = tk.StringVar(value="")
        self.sponsors_var = tk.StringVar(value="")
        self.speaker_mode_var = tk.StringVar(value="single")
        self.glossary_var = tk.StringVar(value="")
        self.event_id_var = tk.StringVar(value="")

        self._listeners: List[Callable[[], None]] = []
        # Separate listeners for the manifest-saved signal so Stage 3 can
        # reload immediately when Stage 2 writes a new manifest.json.
        self._manifest_saved_listeners: List[Callable[[], None]] = []

        for var in (
            self.event_dir_var,
            self.event_name_var,
            self.event_date_var,
            self.organiser_var,
            self.venue_var,
        ):
            var.trace_add("write", self._notify)

    def required_ready(self) -> bool:
        return bool(
            self.event_dir_var.get().strip()
            and self.event_name_var.get().strip()
            and self.event_date_var.get().strip()
        )

    def missing_optional_warnings(self) -> List[str]:
        warnings: List[str] = []
        if not self.organiser_var.get().strip():
            warnings.append("Organiser is blank (required later for the technical document).")
        if not self.venue_var.get().strip():
            warnings.append("Venue is blank (required later for the technical document).")
        return warnings

    def bind_change(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def bind_manifest_saved(self, callback: Callable[[], None]) -> None:
        """Register a callback that fires when Stage 2 saves manifest.json."""
        self._manifest_saved_listeners.append(callback)

    def notify_manifest_saved(self) -> None:
        """Called by Stage 2 after successfully writing manifest.json."""
        for cb in list(self._manifest_saved_listeners):
            cb()

    def _notify(self, *_args) -> None:
        dead = []
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                dead.append(cb)
        for d in dead:
            try:
                self._listeners.remove(d)
            except ValueError:
                pass
