"""
Stage 3 Tab: Transcription (Test Gate First, then Full Run)
Tkinter / ttk implementation with isolated subprocess execution, resume support,
and real-time progress / speed tracking.
"""

import os
import sys
import json
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

from scribe_core.transcription_manager import TranscriptionManager

class Stage3Tab(ttk.Frame):
    """Stage 3 Tab UI and Event Handler."""

    def __init__(self, parent, status_callback=None):
        super().__init__(parent, padding=10)
        self.status_callback = status_callback or (lambda msg: None)
        self.transcription_mgr = TranscriptionManager(python_executable=sys.executable)

        # State
        self.manifest_data = {}
        self.clips = []
        self.is_running = False

        self._build_ui()

    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Upper Frame: Event & Manifest Configuration
        upper = ttk.LabelFrame(paned, text=" 1. Event & Manifest Selection ", padding=10)
        paned.add(upper, weight=1)

        row0 = ttk.Frame(upper)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="Event Folder:", width=14, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.event_dir_var = tk.StringVar(value=r"D:\Events\17.ElevateUai_cybersecurity")
        ttk.Entry(row0, textvariable=self.event_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row0, text="Load Manifest", command=self._load_manifest).pack(side=tk.LEFT, padx=3)

        # Event summary banner
        self.event_summary_lbl = ttk.Label(upper, text="Manifest not loaded.", font=("Segoe UI", 9, "italic"), foreground="#555")
        self.event_summary_lbl.pack(anchor=tk.W, pady=(2, 6))

        # Options Row: Language & Glossary
        opt_frame = ttk.Frame(upper)
        opt_frame.pack(fill=tk.X, pady=4)

        ttk.Label(opt_frame, text="Language:").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value="en")
        lang_cb = ttk.Combobox(opt_frame, textvariable=self.lang_var, values=["en", "auto", "hi"], width=8, state="readonly")
        lang_cb.pack(side=tk.LEFT, padx=5)

        ttk.Label(opt_frame, text="Domain Glossary / Initial Prompt:").pack(side=tk.LEFT, padx=(15, 5))
        self.glossary_var = tk.StringVar(value="cybersecurity, SOC, DFIR, GRC, elevateU, Quality Thought, SIEM, phishing, malware")
        ttk.Entry(opt_frame, textvariable=self.glossary_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # Middle Frame: Test Gate & Clip Selection
        middle = ttk.LabelFrame(paned, text=" 2. Test Gate (Mandatory Pre-Run Verification) ", padding=10)
        paned.add(middle, weight=3)

        gate_ctrl = ttk.Frame(middle)
        gate_ctrl.pack(fill=tk.X, pady=4)

        # Gate status indicator
        self.gate_status_var = tk.StringVar(value="[LOCKED] Test gate not yet approved.")
        self.gate_indicator = ttk.Label(
            gate_ctrl,
            textvariable=self.gate_status_var,
            font=("Segoe UI", 9, "bold"),
            foreground="#d9534f"
        )
        self.gate_indicator.pack(side=tk.LEFT, padx=5)

        self.btn_run_test = ttk.Button(gate_ctrl, text="Transcribe 1-2 Test Clips", command=self._run_test_transcription)
        self.btn_run_test.pack(side=tk.LEFT, padx=10)

        self.btn_approve_test = ttk.Button(gate_ctrl, text="Approve Test & Unlock Full Run", command=self._approve_test_gate)
        self.btn_approve_test.pack(side=tk.LEFT, padx=5)

        self.btn_run_full = ttk.Button(
            gate_ctrl,
            text="Run All Clips (Full Run)",
            command=self._run_full_transcription,
            state=tk.DISABLED
        )
        self.btn_run_full.pack(side=tk.RIGHT, padx=5)

        self.btn_cancel = ttk.Button(gate_ctrl, text="Cancel / Stop", command=self._cancel_run, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.RIGHT, padx=5)

        # Clip Selection Tree
        tree_frame = ttk.Frame(middle)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.clip_tree = ttk.Treeview(
            tree_frame,
            columns=("check", "idx", "clip", "speaker", "duration", "start", "status"),
            show="headings",
            selectmode="extended",
            height=6
        )
        self.clip_tree.heading("check", text="Test")
        self.clip_tree.heading("idx", text="#")
        self.clip_tree.heading("clip", text="Clip Filename")
        self.clip_tree.heading("speaker", text="Speaker")
        self.clip_tree.heading("duration", text="Duration")
        self.clip_tree.heading("start", text="Start Time (IST)")
        self.clip_tree.heading("status", text="Status")

        self.clip_tree.column("check", width=45, anchor=tk.CENTER)
        self.clip_tree.column("idx", width=35, anchor=tk.CENTER)
        self.clip_tree.column("clip", width=180)
        self.clip_tree.column("speaker", width=110)
        self.clip_tree.column("duration", width=70, anchor=tk.CENTER)
        self.clip_tree.column("start", width=120, anchor=tk.CENTER)
        self.clip_tree.column("status", width=110)

        scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.clip_tree.yview)
        self.clip_tree.configure(yscrollcommand=scroll_y.set)
        self.clip_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # Lower Frame: Live Output & Results Viewer
        lower = ttk.LabelFrame(paned, text=" 3. Progress, Live Logs & Transcript Preview ", padding=10)
        paned.add(lower, weight=2)

        # Progress bar & metrics row
        metrics_row = ttk.Frame(lower)
        metrics_row.pack(fill=tk.X, pady=2)

        self.prog_bar = ttk.Progressbar(metrics_row, orient=tk.HORIZONTAL, mode="determinate")
        self.prog_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.speed_lbl_var = tk.StringVar(value="Speed: --x real-time")
        ttk.Label(metrics_row, textvariable=self.speed_lbl_var, font=("Segoe UI", 9, "bold"), foreground="#0275d8").pack(side=tk.RIGHT, padx=5)

        # Text output viewer
        self.log_text = scrolledtext.ScrolledText(lower, height=7, font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=4)

        # Auto-load manifest if exists
        self.after(500, self._load_manifest)

    def _get_paths(self):
        event_dir = self.event_dir_var.get().strip()
        scribe_dir = os.path.join(event_dir, "scribe")
        test_dir = os.path.join(scribe_dir, "test")
        full_dir = os.path.join(scribe_dir, "transcripts")
        work_dir = os.path.join(scribe_dir, "work")
        log_dir = os.path.join(scribe_dir, "logs")
        manifest_path = os.path.join(scribe_dir, "manifest.json")
        return event_dir, scribe_dir, test_dir, full_dir, work_dir, log_dir, manifest_path

    def _load_manifest(self):
        _, scribe_dir, _, _, _, _, manifest_path = self._get_paths()
        if not os.path.exists(manifest_path):
            self.event_summary_lbl.configure(text=f"No manifest.json in {scribe_dir}. Run Stage 2 first.", foreground="#d9534f")
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                self.manifest_data = json.load(f)

            ev = self.manifest_data.get("event", {})
            self.event_summary_lbl.configure(
                text=f"Loaded: {ev.get('name')} | Date: {ev.get('date')} | Sessions: {len(self.manifest_data.get('sessions', []))}",
                foreground="#28a745"
            )

            # Flatten clips from sessions
            self.clips = []
            for s in self.manifest_data.get("sessions", []):
                spk = s.get("speaker", "Speaker")
                for c in s.get("clips", []):
                    c["speaker"] = spk
                    self.clips.append(c)

            # Populate tree
            for item in self.clip_tree.get_children():
                self.clip_tree.delete(item)

            for idx, c in enumerate(self.clips, 1):
                # Mark first 2 clips for test by default
                test_mark = "[TEST]" if idx in (1, 2) else ""
                self.clip_tree.insert(
                    "",
                    tk.END,
                    iid=str(idx),
                    values=(
                        test_mark,
                        idx,
                        c.get("name"),
                        c.get("speaker"),
                        f"{c.get('duration', 0):.1f}s",
                        c.get("start_time", "").split("T")[-1][:8],
                        "Pending"
                    )
                )

            # Check test gate status
            self._update_gate_ui()

        except Exception as e:
            messagebox.showerror("Error Loading Manifest", str(e))

    def _update_gate_ui(self):
        _, scribe_dir, _, _, _, _, _ = self._get_paths()
        approved = TranscriptionManager.is_test_approved(scribe_dir)
        if approved:
            self.gate_status_var.set("[APPROVED] Test Gate Passed. Full Run Unlocked.")
            self.gate_indicator.configure(foreground="#28a745")
            self.btn_run_full.configure(state=tk.NORMAL)
        else:
            self.gate_status_var.set("[LOCKED] Test Gate Required. Run and approve 1-2 test clips first.")
            self.gate_indicator.configure(foreground="#d9534f")
            self.btn_run_full.configure(state=tk.DISABLED)

    def _approve_test_gate(self):
        _, scribe_dir, test_dir, _, _, _, _ = self._get_paths()
        # Check if test files exist
        if not os.path.exists(test_dir) or not os.listdir(test_dir):
            messagebox.showwarning("Test Gate", "No test transcripts found in scribe\\test\\. Please run test transcription first.")
            return

        confirm = messagebox.askyesno(
            "Approve Test Transcripts",
            "Are the test transcripts accurate and verified?\nApproving will unlock the Full Run button."
        )
        if confirm:
            TranscriptionManager.set_test_approved(scribe_dir, approved=True, notes="Approved by user in UI.")
            self._update_gate_ui()
            messagebox.showinfo("Approved", "Test Gate Approved! 'Run All Clips (Full Run)' is now unlocked.")

    def _log(self, text: str):
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _run_test_transcription(self):
        if self.is_running:
            return

        # Pick first 2 clips or selected clips
        selected_ids = self.clip_tree.selection()
        if selected_ids:
            test_clips = [self.clips[int(i) - 1] for i in selected_ids[:2]]
        else:
            test_clips = self.clips[:2]

        if not test_clips:
            messagebox.showwarning("No Clips", "Please load manifest with video clips first.")
            return

        event_dir, scribe_dir, test_dir, _, work_dir, log_dir, _ = self._get_paths()
        self._start_transcription_job(
            clips_to_run=test_clips,
            output_dir=test_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            job_name="Test Gate Run"
        )

    def _run_full_transcription(self):
        if self.is_running:
            return

        _, scribe_dir, _, full_dir, work_dir, log_dir, _ = self._get_paths()
        if not TranscriptionManager.is_test_approved(scribe_dir):
            messagebox.showwarning("Locked", "Test gate is locked. Run and approve test clips first.")
            return

        self._start_transcription_job(
            clips_to_run=self.clips,
            output_dir=full_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            job_name="Full Event Run"
        )

    def _start_transcription_job(self, clips_to_run, output_dir, work_dir, log_dir, job_name):
        self.is_running = True
        self.btn_run_test.configure(state=tk.DISABLED)
        self.btn_run_full.configure(state=tk.DISABLED)
        self.btn_approve_test.configure(state=tk.DISABLED)
        self.btn_cancel.configure(state=tk.NORMAL)

        self.prog_bar["value"] = 0
        self.prog_bar["maximum"] = len(clips_to_run)
        self._log(f"\n{'='*60}\nStarting {job_name} ({len(clips_to_run)} clips) into:\n{output_dir}\n{'='*60}")

        lang = None if self.lang_var.get() == "auto" else self.lang_var.get()
        prompt = self.glossary_var.get().strip() or None

        config_path = self.transcription_mgr.prepare_job_config(
            event_dict=self.manifest_data.get("event", {}),
            clips_to_run=clips_to_run,
            output_dir=output_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            language=lang or "en",
            initial_prompt=prompt,
            model="medium"
        )

        threading.Thread(
            target=self._worker_thread,
            args=(config_path, clips_to_run, output_dir),
            daemon=True
        ).start()

    def _worker_thread(self, config_path, clips_to_run, output_dir):
        def on_event(ev):
            self.after(0, self._handle_worker_event, ev)

        try:
            summary = self.transcription_mgr.run_worker_blocking(config_path, progress_callback=on_event)
            self.after(0, self._on_job_finished, summary, output_dir)
        except Exception as e:
            self.after(0, lambda err=e: self._on_job_error(str(err)))

    def _handle_worker_event(self, ev: dict):
        t = ev.get("type")
        if t == "model_loaded":
            self._log(f"[INIT] Model '{ev.get('model')}' loaded on {ev.get('device')} in {ev.get('load_time')}s.")
        elif t == "clip_start":
            self._log(f"[{ev.get('index')}/{ev.get('total')}] Transcribing '{ev.get('clip')}' ({ev.get('speaker')})...")
        elif t == "clip_complete":
            self.prog_bar["value"] = ev.get("index", 0)
            dur = ev.get("audio_duration", 0)
            elapsed = ev.get("transcribe_time", 0)
            speed = ev.get("speed_factor", 0)
            self.speed_lbl_var.set(f"Speed: {speed}x real-time")
            self._log(
                f"[DONE] {ev.get('clip')}: Audio {dur:.1f}s transcribed in {elapsed:.2f}s "
                f"({speed}x real-time) -> {ev.get('segments_count')} segments"
            )
        elif t == "clip_skipped":
            self.prog_bar["value"] = ev.get("index", 0)
            self._log(f"[SKIP] {ev.get('clip')} skipped (transcript already exists).")
        elif t == "clip_error":
            self._log(f"[ERROR] Failed {ev.get('clip')}: {ev.get('error')}")

    def _on_job_finished(self, summary, output_dir):
        self.is_running = False
        self.btn_run_test.configure(state=tk.NORMAL)
        self.btn_approve_test.configure(state=tk.NORMAL)
        self.btn_cancel.configure(state=tk.DISABLED)
        self._update_gate_ui()

        msg = (
            f"Run Completed Successfully!\n\n"
            f"• Processed: {summary.get('processed_count', 0)}\n"
            f"• Skipped (Resumed): {summary.get('skipped_count', 0)}\n"
            f"• Total Run Time: {summary.get('total_run_time_str')}\n"
            f"• Total Audio: {summary.get('total_audio_duration_sec', 0):.1f}s\n"
            f"• Speed Factor: {summary.get('overall_speed_factor', 0)}x real-time\n"
            f"• Flagged Low-Confidence Lines: {summary.get('low_confidence_flags_count', 0)}\n"
            f"• Output Folder: {output_dir}"
        )
        self._log(f"\n{msg}\n")
        messagebox.showinfo("Transcription Run Complete", msg)

    def _on_job_error(self, err_msg):
        self.is_running = False
        self.btn_run_test.configure(state=tk.NORMAL)
        self.btn_approve_test.configure(state=tk.NORMAL)
        self.btn_cancel.configure(state=tk.DISABLED)
        self._update_gate_ui()
        self._log(f"\n[JOB FAILURE]: {err_msg}\n")
        messagebox.showerror("Transcription Job Failed", err_msg)

    def _cancel_run(self):
        if self.is_running:
            self.transcription_mgr.cancel()
            self._log("[CANCEL] Sent termination signal to worker process.")
