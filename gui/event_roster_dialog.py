"""
gui/event_roster_dialog.py
Dialog for configuring the Event Speaker Roster from enrolled Vault v2 people.

Security Invariants:
1. One person = one persistent spk_ ID across all sessions and events.
2. Quarantined profiles NEVER appear in roster selection.
3. Names are visible only while Vault v2 is unlocked; while locked, aliases/masked labels appear.
4. Per-event aliases (S01, S02, ...) are mapped to spk_ IDs in Vault v2 Identity Zone.
5. Zero plaintext leaks: Dialog builds once, uses theme tokens only.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Dict, List, Optional

from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.multi_speaker import ALLOWED_ROLES
from . import theme
from .scrollable_tab import attach_universal_scrolling


class EventRosterDialog(tk.Toplevel):
    """
    Dialog to manage the event-level roster by selecting enrolled profiles from Vault v2.
    Assigns sequential per-event aliases (S01, S02, ...).
    """

    def __init__(
        self,
        parent: tk.Misc,
        event_id: str,
        roster_mgr: EventRosterManager,
        on_save_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        super().__init__(parent)
        self.event_id = event_id
        self.roster_mgr = roster_mgr
        self.storage = roster_mgr.storage
        self.vault = roster_mgr.vault
        self.on_save_callback = on_save_callback

        self.title("Event Speaker Roster — Vault v2 Integration")
        self.minsize(700, 520)
        self.resizable(True, True)
        self.configure(bg=theme.COLOR_APP_BG)
        self.transient(parent)
        self.grab_set()

        # State
        self.event_roster: List[Dict[str, Any]] = self.roster_mgr.load_event_roster(self.event_id)
        self.available_people: List[Dict[str, Any]] = self.roster_mgr.get_available_vault_people()

        self._build_ui()
        self._refresh_tables()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _e: self.destroy())
        theme.center_dialog(self, parent, width=840, height=660, min_width=700, min_height=520)

    def _on_configure(self, event) -> None:
        if event.widget == self:
            avail = max(240, event.width - 48)
            if hasattr(self, "lbl_info") and self.lbl_info.winfo_exists():
                self.lbl_info.configure(wraplength=avail)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # 3. Footer Action Buttons (packed first at bottom to ensure visibility)
        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, side="bottom", pady=(10, 0))

        lbl_summary = ttk.Label(
            footer,
            text=f"Total Event Speakers: {len(self.event_roster)}",
            font=("Segoe UI", 9, "bold"),
        )
        self.lbl_summary = lbl_summary
        lbl_summary.pack(side=tk.LEFT)

        btn_box = ttk.Frame(footer)
        btn_box.pack(side=tk.RIGHT)

        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_box, text="Save Event Roster", style="Accent.TButton", command=self._save_roster).pack(
            side=tk.RIGHT, padx=4
        )

        # 1. Header Banner
        header_card = ttk.Frame(container)
        header_card.pack(fill=tk.X, pady=(0, 8))

        lbl_title = ttk.Label(
            header_card,
            text=f"Event Speaker Roster ({self.event_id})",
            font=("Segoe UI", 12, "bold"),
            foreground=theme.COLOR_HEADER_NAVY,
        )
        lbl_title.pack(anchor=tk.W)

        is_unlocked = self.vault.is_unlocked()
        info_text = (
            "Pick enrolled people from your secure Vault v2 to assign to this event. "
            "Each person is assigned an isolated per-event alias (S01, S02, ...) so compute-zone files "
            "never carry personal names or persistent IDs."
        )
        if not is_unlocked:
            info_text += "\n⚠️ VAULT IS LOCKED: Personal names are hidden. Unlock Vault to view names."

        self.lbl_info = ttk.Label(
            header_card,
            text=info_text,
            font=("Segoe UI", 9),
            foreground=theme.COLOR_TEXT_PRIMARY if is_unlocked else theme.COLOR_ALERT_ORANGE,
            wraplength=780,
        )
        self.lbl_info.pack(anchor=tk.W, fill=tk.X, pady=(2, 6))

        # 2. Main Dual-Pane Section (Available Vault People on Left | Event Roster on Right)
        dual_pane = ttk.Frame(container)
        dual_pane.pack(fill=tk.BOTH, expand=True, pady=4)
        dual_pane.columnconfigure(0, weight=1)
        dual_pane.columnconfigure(1, weight=0)
        dual_pane.columnconfigure(2, weight=1)
        dual_pane.rowconfigure(0, weight=1)

        # Left Pane: Available Vault People
        left_frame = ttk.LabelFrame(dual_pane, text=" Enrolled Vault People ", padding=8)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        tree_left_frame = ttk.Frame(left_frame)
        tree_left_frame.pack(fill=tk.BOTH, expand=True)

        self.tree_vault = ttk.Treeview(
            tree_left_frame,
            columns=("name", "samples", "roles"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.tree_vault.heading("name", text="Speaker Name")
        self.tree_vault.heading("samples", text="Samples")
        self.tree_vault.heading("roles", text="Default Roles")

        self.tree_vault.column("name", width=170, minwidth=120)
        self.tree_vault.column("samples", width=60, minwidth=50, anchor=tk.CENTER)
        self.tree_vault.column("roles", width=110, minwidth=80)

        scroll_left = ttk.Scrollbar(tree_left_frame, orient=tk.VERTICAL, command=self.tree_vault.yview)
        self.tree_vault.configure(yscrollcommand=scroll_left.set)
        self.tree_vault.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_left.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_vault.bind("<<TreeviewSelect>>", self._on_select_vault_person)

        # Left Pane Vault Actions: Move to Quarantine and Quarantine Manager
        vault_btn_bar = ttk.Frame(left_frame)
        vault_btn_bar.pack(fill=tk.X, pady=(6, 0))

        self.btn_quarantine_profile = ttk.Button(
            vault_btn_bar,
            text="Quarantine Profile...",
            command=self._on_quarantine_profile,
            state=tk.DISABLED,
        )
        self.btn_quarantine_profile.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_quarantine_mgr = ttk.Button(
            vault_btn_bar,
            text="Quarantine Manager...",
            command=self._on_open_quarantine_manager,
        )
        self.btn_quarantine_mgr.pack(side=tk.LEFT)

        # Center Action Buttons
        center_btns = ttk.Frame(dual_pane, padding=6)
        center_btns.grid(row=0, column=1, sticky="ns", padx=4)

        ttk.Label(center_btns, text="").pack(fill=tk.Y, expand=True)
        self.btn_add = ttk.Button(
            center_btns,
            text="Add to Event ➔",
            command=self._add_to_event_roster,
            state=tk.DISABLED,
        )
        self.btn_add.pack(pady=4)

        self.btn_remove = ttk.Button(
            center_btns,
            text="⬅ Remove",
            command=self._remove_from_event_roster,
            state=tk.DISABLED,
        )
        self.btn_remove.pack(pady=4)
        ttk.Label(center_btns, text="").pack(fill=tk.Y, expand=True)

        # Right Pane: Current Event Roster
        right_frame = ttk.LabelFrame(dual_pane, text=" Event Roster (Assigned Aliases) ", padding=8)
        right_frame.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        tree_right_frame = ttk.Frame(right_frame)
        tree_right_frame.pack(fill=tk.BOTH, expand=True)

        self.tree_roster = ttk.Treeview(
            tree_right_frame,
            columns=("alias", "name", "role"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.tree_roster.heading("alias", text="Alias")
        self.tree_roster.heading("name", text="Speaker Name")
        self.tree_roster.heading("role", text="Event Role")

        self.tree_roster.column("alias", width=60, minwidth=50, anchor=tk.CENTER)
        self.tree_roster.column("name", width=170, minwidth=120)
        self.tree_roster.column("role", width=110, minwidth=80)

        scroll_right = ttk.Scrollbar(tree_right_frame, orient=tk.VERTICAL, command=self.tree_roster.yview)
        self.tree_roster.configure(yscrollcommand=scroll_right.set)
        self.tree_roster.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_right.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_roster.bind("<<TreeviewSelect>>", self._on_select_roster_person)

        # Right Pane toolbar (role selector)
        role_bar = ttk.Frame(right_frame)
        role_bar.pack(fill=tk.X, pady=(6, 0))

        ttk.Label(role_bar, text="Role:").pack(side=tk.LEFT, padx=(0, 4))
        self.combo_role = ttk.Combobox(role_bar, values=ALLOWED_ROLES, state="readonly", width=12)
        self.combo_role.set("Speaker")
        self.combo_role.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_change_role = ttk.Button(
            role_bar,
            text="Change Role",
            command=self._change_selected_role,
            state=tk.DISABLED,
        )
        self.btn_change_role.pack(side=tk.LEFT)

    def _refresh_tables(self) -> None:
        """Populates left and right treeviews."""
        # Refresh Left: Available Vault People
        self.tree_vault.delete(*self.tree_vault.get_children())
        assigned_spks = {r["spk_id"] for r in self.event_roster}

        for p in self.available_people:
            # Show in left pane only if not already assigned to event
            if p["spk_id"] not in assigned_spks:
                roles_str = ", ".join(p.get("roles", []))
                self.tree_vault.insert(
                    "",
                    tk.END,
                    iid=p["spk_id"],
                    values=(p["display_name"], p["sample_count"], roles_str),
                )

        # Refresh Right: Event Roster
        self.tree_roster.delete(*self.tree_roster.get_children())
        for r in self.event_roster:
            alias = r.get("alias", "")
            self.tree_roster.insert(
                "",
                tk.END,
                iid=alias,
                values=(alias, r.get("name", ""), r.get("role", "Speaker")),
            )

        if hasattr(self, "lbl_summary"):
            self.lbl_summary.configure(text=f"Total Event Speakers: {len(self.event_roster)}")

    def _on_select_vault_person(self, _event) -> None:
        sel = self.tree_vault.selection()
        self.btn_add.configure(state=tk.NORMAL if sel else tk.DISABLED)
        if hasattr(self, "btn_quarantine_profile"):
            self.btn_quarantine_profile.configure(state=tk.NORMAL if sel else tk.DISABLED)

    def _on_quarantine_profile(self) -> None:
        sel = self.tree_vault.selection()
        if not sel:
            return
        spk_id = sel[0]
        from scribe_core.quarantine_manager import QuarantineManager
        from gui.vault_dialog import DestructiveConfirmationDialog
        qm = QuarantineManager(self.vault)
        ok, msg, summary = qm.prepare_quarantine_confirmation(spk_id)
        if not ok:
            messagebox.showwarning("Quarantine Error", msg, parent=self)
            return

        def _after_quarantined():
            self.available_people = self.roster_mgr.get_available_vault_people()
            self._refresh_tables()

        DestructiveConfirmationDialog(
            self,
            spk_id=spk_id,
            name=summary.get("name", spk_id),
            sample_count=summary.get("sample_count", 0),
            confirmation_code=summary.get("confirmation_code", "CF-0000"),
            quarantine_mgr=qm,
            on_success=_after_quarantined,
        )

    def _on_open_quarantine_manager(self) -> None:
        from scribe_core.quarantine_manager import QuarantineManager
        from gui.vault_dialog import QuarantineManagerDialog
        qm = QuarantineManager(self.vault)

        def _after_changed():
            self.available_people = self.roster_mgr.get_available_vault_people()
            self._refresh_tables()

        dlg = QuarantineManagerDialog(self, qm, on_changed=_after_changed)
        self.wait_window(dlg)

    def _on_select_roster_person(self, _event) -> None:
        sel = self.tree_roster.selection()
        if sel:
            self.btn_remove.configure(state=tk.NORMAL)
            self.btn_change_role.configure(state=tk.NORMAL)
            alias = sel[0]
            for r in self.event_roster:
                if r.get("alias") == alias:
                    self.combo_role.set(r.get("role", "Speaker"))
                    break
        else:
            self.btn_remove.configure(state=tk.DISABLED)
            self.btn_change_role.configure(state=tk.DISABLED)

    def _add_to_event_roster(self) -> None:
        sel = self.tree_vault.selection()
        if not sel:
            return

        spk_id = sel[0]
        person = next((p for p in self.available_people if p["spk_id"] == spk_id), None)
        if not person:
            return

        existing_aliases = [r.get("alias", "") for r in self.event_roster]
        next_alias = EventRosterManager.assign_next_alias(existing_aliases)
        default_role = person.get("roles", ["Speaker"])[0] if person.get("roles") else "Speaker"

        new_item = {
            "alias": next_alias,
            "spk_id": spk_id,
            "name": person.get("display_name", next_alias),
            "role": default_role,
        }
        self.event_roster.append(new_item)
        self._refresh_tables()

    def _remove_from_event_roster(self) -> None:
        sel = self.tree_roster.selection()
        if not sel:
            return

        alias_to_remove = sel[0]
        self.event_roster = [r for r in self.event_roster if r.get("alias") != alias_to_remove]
        self._refresh_tables()

    def _change_selected_role(self) -> None:
        sel = self.tree_roster.selection()
        if not sel:
            return

        alias = sel[0]
        new_role = self.combo_role.get().strip() or "Speaker"
        for r in self.event_roster:
            if r.get("alias") == alias:
                r["role"] = new_role
                break

        self._refresh_tables()

    def _save_roster(self) -> None:
        try:
            self.roster_mgr.save_event_roster(self.event_id, self.event_roster)
            if self.on_save_callback:
                self.on_save_callback(self.event_roster)
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showinfo(
                    "Event Roster Saved",
                    f"Saved {len(self.event_roster)} speakers into encrypted Vault v2 alias map.",
                    parent=self,
                )
            self.destroy()
        except Exception as e:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showerror("Save Error", f"Failed to save event roster: {e}", parent=self)
