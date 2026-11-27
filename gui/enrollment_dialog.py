"""
gui/enrollment_dialog.py
Interactive Voice Profile Enrollment Dialog for Session Scribe Vault v2.

Features:
- Vault Unlocked Guard: Only opens and operates while Voice Vault is confirmed unlocked.
- Step 1: Real Name, Session Roles, and mandatory auditable consent confirmation.
- Step 2: Multi-sample import (audio/video files) with optional time range (start/end seconds).
- Step 3: Pyannote acoustic quality check report (speech length, SNR, single-speaker, overlap).
  Clear failures rejected; borderline results display exact numbers and offer "Accept Anyway" with explicit user confirmation.
- Step 4: Cross-sample consistency and duplicate-profile warning banner.
- Step 5: Per-sample user approval gate and target tracker (3-5 samples, 2-3 minutes total).
- Step 6: Secure dual-zone commitment into Vault v2 (AES-256-GCM encrypted WAVs, embeddings, model version).

UI Guarantees:
- Build once, update in place (zero rebuild on resize or theme events).
- Design System v2 tokens (WCAG AA contrast).
- Zero plaintext audio or secrets written to disk or clipboard.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Callable

from gui import theme
from scribe_core.enrollment_manager import (
    EnrollmentManager,
    EnrollmentSession,
    MIN_ENROLLMENT_SAMPLES,
    MAX_ENROLLMENT_SAMPLES,
)
from scribe_core.voice_quality_checker import QualityCheckResult


class VoiceProfileEnrollmentDialog(tk.Toplevel):
    """
    Dialog for enrolling a speaker into the secure Vault v2 voice library.
    Strictly requires the vault to be unlocked.
    """

    def __init__(
        self,
        parent,
        enrollment_mgr: EnrollmentManager,
        notify_fn: Optional[Callable[[str, str], None]] = None,
        confirm_fn: Optional[Callable[[str, str], bool]] = None,
    ):
        super().__init__(parent)
        self.mgr = enrollment_mgr
        self.session: Optional[EnrollmentSession] = None
        self.success = False
        self.notify_fn = notify_fn
        self.confirm_fn = confirm_fn

        # Immediate Security Guard: Only accessible while unlocked
        if not self.mgr.vault.is_unlocked():
            if self.notify_fn:
                self.notify_fn(
                    "Voice Vault Locked",
                    "Voice Vault is locked. Voice profile enrollment is only accessible while the vault is unlocked with your passphrase.",
                )
            self.destroy()
            return

        self.title("Voice Profile Enrollment — Vault v2")
        self.geometry("700x660")
        self.minsize(600, 540)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._pending_sample = None
        self._pending_borderline_report: Optional[QualityCheckResult] = None
        self._pending_borderline_path: Optional[str] = None
        self._pending_borderline_range: Optional[tuple[float, float]] = None

        self._build_ui()
        self.center_window()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=720, height=680, min_width=600, min_height=540)

    def _on_configure(self, event) -> None:
        if event.widget == self:
            avail = max(240, event.width - 40)
            if hasattr(self, "_lbl_title") and self._lbl_title.winfo_exists():
                self._lbl_title.configure(wraplength=avail)

    def _build_ui(self) -> None:
        self.container = ttk.Frame(self, padding=16)
        self.container.pack(fill="both", expand=True)

        # Footer packed first at bottom so it is never clipped
        footer = ttk.Frame(self.container)
        footer.pack(fill="x", side="bottom")

        self.btn_cancel = ttk.Button(footer, text="Cancel", command=self.destroy)
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_commit = ttk.Button(
            footer,
            text="Commit & Enrol in Vault",
            command=self._on_commit,
            style="Accent.TButton",
            state="disabled",
        )
        self.btn_commit.pack(side="right")

        self._lbl_title = ttk.Label(
            self.container,
            text="Voice Profile Enrollment (Vault v2)\nImport 3 to 5 reference samples (2 to 3 minutes total speech recommended). All biometric data is encrypted in Zone 2.",
            font=("Segoe UI", 10, "bold"),
            wraplength=660,
        )
        self._lbl_title.pack(fill="x", pady=(0, 8))

        # Duplicate Warning Banner (hidden until text is set)
        self.dup_banner = ttk.Label(
            self.container,
            text="",
            foreground="#D35400",
            font=("Segoe UI", 9, "bold"),
            wraplength=660,
        )

        # --- Section 1: Person Identity & Consent ---
        id_card = ttk.LabelFrame(self.container, text="1. Speaker Identity & Auditable Consent", padding=10)
        id_card.pack(fill="x", pady=(0, 8))
        self.id_card = id_card

        row1 = ttk.Frame(id_card)
        row1.pack(fill="x", pady=(0, 4))
        ttk.Label(row1, text="Full Name: ", width=12).pack(side="left")
        self.name_var = tk.StringVar()
        self.entry_name = ttk.Entry(row1, textvariable=self.name_var, width=32)
        self.entry_name.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry_name.focus_set()

        # Consent Checkbox
        self.consent_var = tk.BooleanVar(value=False)
        self.chk_consent = ttk.Checkbutton(
            id_card,
            text="I confirm that explicit consent has been noted for this speaker to store voice biometrics locally.",
            variable=self.consent_var,
            command=self._update_progress_and_commit_state,
        )
        self.chk_consent.pack(anchor="w", pady=(4, 0))

        # --- Section 2: Sample Import & Quality Inspection ---
        import_card = ttk.LabelFrame(self.container, text="2. Import Sample Audio / Video", padding=10)
        import_card.pack(fill="x", pady=(0, 8))

        row_file = ttk.Frame(import_card)
        row_file.pack(fill="x", pady=(0, 4))
        ttk.Label(row_file, text="Media File: ", width=12).pack(side="left")
        self.file_var = tk.StringVar()
        self.entry_file = ttk.Entry(row_file, textvariable=self.file_var)
        self.entry_file.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row_file, text="Browse...", command=self._on_browse_file).pack(side="left")

        # Time Range
        row_time = ttk.Frame(import_card)
        row_time.pack(fill="x", pady=(0, 6))
        ttk.Label(row_time, text="Range (s): ", width=12).pack(side="left")
        ttk.Label(row_time, text="Start:").pack(side="left", padx=(0, 4))
        self.start_var = tk.StringVar(value="")
        ttk.Entry(row_time, textvariable=self.start_var, width=8).pack(side="left", padx=(0, 8))
        ttk.Label(row_time, text="End:").pack(side="left", padx=(0, 4))
        self.end_var = tk.StringVar(value="")
        ttk.Entry(row_time, textvariable=self.end_var, width=8).pack(side="left", padx=(0, 12))
        ttk.Label(row_time, text="(Optional: leave blank for entire clip)", foreground="#666666").pack(side="left")

        # Import & Approval actions
        row_act = ttk.Frame(import_card)
        row_act.pack(fill="x", pady=(2, 4))
        self.btn_inspect = ttk.Button(row_act, text="🔍 Inspect Sample", command=self._on_inspect_sample)
        self.btn_inspect.pack(side="left", padx=(0, 8))

        self.btn_stage_sample = ttk.Button(
            row_act,
            text="✅ Approve & Stage Sample",
            command=self._on_approve_and_stage,
            state="disabled",
        )
        self.btn_stage_sample.pack(side="left", padx=(0, 8))

        self.btn_accept_borderline = ttk.Button(
            row_act,
            text="⚠️ Override & Stage (Borderline)",
            command=self._on_override_borderline,
            state="disabled",
        )
        self.btn_accept_borderline.pack(side="left")

        self.quality_lbl = ttk.Label(import_card, text="", wraplength=640, font=("Segoe UI", 9))
        # Not packed initially

        # --- Section 3: Staged Samples Table & Approval ---
        samples_card = ttk.LabelFrame(self.container, text="3. Sample Approval & Target Progress", padding=10)
        samples_card.pack(fill="both", expand=True, pady=(0, 10))

        # Progress header
        self.progress_lbl = ttk.Label(
            samples_card,
            text="Target: 3-5 samples (minimum 10s speech each) | Approved: 0 / 5 | Total Speech: 0.0s",
            font=("Segoe UI", 9, "bold"),
        )
        self.progress_lbl.pack(anchor="w", pady=(0, 6))

        # Samples list frame
        self.sample_list_frame = ttk.Frame(samples_card)
        self.sample_list_frame.pack(fill="both", expand=True)
        self.sample_rows: list[ttk.Frame] = []

    def _set_dup_banner(self, text: str) -> None:
        if not text:
            self.dup_banner.config(text="")
            if self.dup_banner.winfo_ismapped():
                self.dup_banner.pack_forget()
            return
        self.dup_banner.config(text=text)
        if not self.dup_banner.winfo_ismapped():
            self.dup_banner.pack(fill="x", pady=(0, 4), before=self.id_card)

    def _set_quality(self, text: str, fg: str = "#C0392B") -> None:
        if not text:
            self.quality_lbl.config(text="")
            if self.quality_lbl.winfo_ismapped():
                self.quality_lbl.pack_forget()
            return
        self.quality_lbl.config(text=text, foreground=fg)
        if not self.quality_lbl.winfo_ismapped():
            self.quality_lbl.pack(fill="x", pady=(4, 0))

    def _on_browse_file(self) -> None:
        types = [
            ("Audio/Video Files", "*.wav;*.mp3;*.m4a;*.mp4;*.mov;*.mkv;*.flac;*.aac"),
            ("All Files", "*.*"),
        ]
        path = filedialog.askopenfilename(parent=self, title="Select Sample Media File", filetypes=types)
        if path:
            self.file_var.set(path)

    def _ensure_session(self) -> bool:
        if self.session is not None:
            return True
        name = self.name_var.get().strip()
        if not name:
            self._set_quality("⚠️ Please enter the speaker's full name first.", "#C0392B")
            self.entry_name.focus_set()
            return False

        if not self.consent_var.get():
            self._set_quality("⚠️ Please check the consent confirmation box first.", "#C0392B")
            return False

        try:
            self.session = self.mgr.start_session(
                full_name=name,
                roles=["Speaker"],
                consent_noted=True,
            )
            return True
        except Exception as e:
            self._set_quality(f"Failed to start enrollment session: {e}", "#C0392B")
            return False

    def _on_inspect_sample(self, override_borderline: bool = False, confirm_inconsistency: bool = False) -> None:
        if not self._ensure_session():
            return

        media_path = self.file_var.get().strip()
        if not media_path or not os.path.isfile(media_path):
            self._set_quality("⚠️ Please select a valid media file.", "#C0392B")
            return

        time_range = None
        s_txt = self.start_var.get().strip()
        e_txt = self.end_var.get().strip()
        if s_txt and e_txt:
            try:
                s_val = float(s_txt)
                e_val = float(e_txt)
                if e_val <= s_val or s_val < 0:
                    self._set_quality("⚠️ End time must be greater than start time.", "#C0392B")
                    return
                time_range = (s_val, e_val)
            except ValueError:
                self._set_quality("⚠️ Start and end times must be valid numbers.", "#C0392B")
                return

        self._set_quality("Analyzing acoustic quality and generating embedding in memory...", "#555555")
        self.btn_stage_sample.config(state="disabled")
        self.btn_accept_borderline.config(state="disabled")
        self.update_idletasks()

        ok, msg, pending, report = self.mgr.inspect_sample(
            session=self.session,
            source_media_path=media_path,
            time_range=time_range,
            override_borderline=override_borderline,
            confirm_inconsistency=confirm_inconsistency,
        )

        if self.session.duplicate_warning:
            self._set_dup_banner(f"⚠️ {self.session.duplicate_warning}")
        else:
            self._set_dup_banner("")

        if ok and pending and report:
            self._pending_sample = pending
            if report.verdict == "CLEAR_PASS":
                self.btn_stage_sample.config(state="normal")
                self.btn_accept_borderline.config(state="disabled")
                self._set_quality(
                    f"✅ Verified: {report.speech_duration_sec:.1f}s speech | SNR {report.snr_db:.1f} dB (1 speaker). Click 'Approve & Stage' to confirm.",
                    "#27AE60",
                )
            elif report.is_borderline:
                self.btn_stage_sample.config(state="disabled")
                self.btn_accept_borderline.config(state="normal")
                self._set_quality(
                    f"⚠️ Borderline Quality ({report.failure_reason}). Click 'Override & Stage' to confirm.",
                    "#D35400",
                )
        else:
            self._pending_sample = None
            if report and report.is_borderline:
                self.btn_stage_sample.config(state="disabled")
                self.btn_accept_borderline.config(state="normal")
                self._set_quality(
                    f"⚠️ Borderline Quality ({report.failure_reason}). Click 'Override & Stage' to allow.",
                    "#D35400",
                )
            elif "inconsistency" in msg.lower():
                confirm = messagebox.askyesno(
                    "Cross-Sample Inconsistency",
                    f"{msg}\n\nDo you want to accept this sample anyway as the same speaker?",
                    parent=self,
                )
                if confirm:
                    self._on_inspect_sample(override_borderline=override_borderline, confirm_inconsistency=True)
                    return
                self._set_quality(f"❌ {msg}", "#C0392B")
            else:
                self._set_quality(f"❌ {msg}", "#C0392B")

    def _on_approve_and_stage(self) -> None:
        """User explicitly clicked 'Approve & Stage' for an inspected sample."""
        if not self._pending_sample or not self.session:
            return
        ok, msg = self.mgr.stage_sample(self.session, self._pending_sample, user_approved=True)
        self._pending_sample = None
        self.btn_stage_sample.config(state="disabled")
        self.btn_accept_borderline.config(state="disabled")
        if ok:
            self._set_quality(f"✅ {msg}", "#27AE60")
            self._render_sample_list()
        else:
            self._set_quality(f"❌ {msg}", "#C0392B")

    def _on_override_borderline(self) -> None:
        """User explicitly clicked 'Override & Stage (Borderline)'."""
        if not self._pending_sample or not self.session:
            return
        self._pending_sample.is_borderline_accepted = True
        ok, msg = self.mgr.stage_sample(self.session, self._pending_sample, user_approved=True)
        self._pending_sample = None
        self.btn_stage_sample.config(state="disabled")
        self.btn_accept_borderline.config(state="disabled")
        if ok:
            self._set_quality(f"⚠️ {msg} (Borderline override recorded in audit trail)", "#27AE60")
            self._render_sample_list()
        else:
            self._set_quality(f"❌ {msg}", "#C0392B")

    def _render_sample_list(self) -> None:
        """Renders the staged samples in place without recreating parent containers."""
        for row in self.sample_rows:
            row.destroy()
        self.sample_rows.clear()

        if not self.session or not self.session.samples:
            self._update_progress_and_commit_state()
            return

        for sample in self.session.samples:
            row = ttk.Frame(self.sample_list_frame)
            row.pack(fill="x", pady=2)
            self.sample_rows.append(row)

            # Approval checkbox
            appr_var = tk.BooleanVar(value=sample.approved_by_user)

            def make_cmd(s_idx=sample.sample_index, v=appr_var):
                return lambda: self._on_toggle_sample_approval(s_idx, v.get())

            chk = ttk.Checkbutton(row, text=sample.sample_name, variable=appr_var, command=make_cmd())
            chk.pack(side="left", padx=(0, 8))

            # Quality details badge
            q = sample.quality_report
            extra = " [Borderline Accepted]" if sample.is_borderline_accepted else ""
            incons = " [Inconsistent]" if sample.is_inconsistent else ""
            info = f"{q.speech_duration_sec:.1f}s speech | SNR {q.snr_db:.1f} dB | {q.speaker_count} spk{extra}{incons}"
            badge_color = "#D35400" if (sample.is_borderline_accepted or sample.is_inconsistent) else theme.COLOR_TEXT_SECONDARY
            lbl_info = ttk.Label(row, text=info, foreground=badge_color, font=("Segoe UI", 9))
            lbl_info.pack(side="left", fill="x", expand=True)

            # Delete button
            def make_del(s_idx=sample.sample_index):
                return lambda: self._on_delete_sample(s_idx)

            btn_del = ttk.Button(row, text="✕", width=3, command=make_del())
            btn_del.pack(side="right")

        self._update_progress_and_commit_state()

    def _on_toggle_sample_approval(self, sample_idx: int, approved: bool) -> None:
        if self.session:
            self.mgr.set_sample_approval(self.session, sample_idx, approved)
            self._update_progress_and_commit_state()

    def _on_delete_sample(self, sample_idx: int) -> None:
        if self.session:
            self.mgr.remove_sample(self.session, sample_idx)
            self._render_sample_list()

    def _update_progress_and_commit_state(self) -> None:
        if not self.session:
            self.progress_lbl.config(text="Target: 3-5 samples (minimum 10s speech each) | Approved: 0 / 5 | Total Speech: 0.0s")
            self.btn_commit.config(state="disabled")
            return

        approved_count = self.session.approved_sample_count
        total_speech = self.session.total_speech_duration
        has_consent = self.consent_var.get()

        status_text = (
            f"Target: {MIN_ENROLLMENT_SAMPLES}-{MAX_ENROLLMENT_SAMPLES} samples | "
            f"Approved: {approved_count} / {MAX_ENROLLMENT_SAMPLES} | "
            f"Total Speech: {total_speech:.1f}s"
        )
        self.progress_lbl.config(text=status_text)

        # Enable commit only when >= 3 approved samples and consent noted
        can_commit = (approved_count >= MIN_ENROLLMENT_SAMPLES) and has_consent
        self.btn_commit.config(state="normal" if can_commit else "disabled")

    def _on_commit(self) -> None:
        if not self.session:
            return

        self._set_quality("Encrypting reference WAVs and committing profile to Vault v2...", "#555555")
        self.update_idletasks()

        ok, msg = self.mgr.commit_enrollment(self.session)
        if ok:
            messagebox.showinfo("Enrollment Complete", msg, parent=self)
            self.success = True
            self.destroy()
        else:
            self._set_quality(f"Commit Error: {msg}", "#C0392B")
