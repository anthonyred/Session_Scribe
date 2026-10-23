"""
Stage 2 Tab: Event Setup, Media Ingest & Separation, Session Grouping & Manifest
Tkinter / ttk implementation with non-blocking threaded execution.
"""

import os
import threading
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from scribe_core.time_parser import TimeParser, IST
from scribe_core.clip_probe import ClipProbe
from scribe_core.session_grouper import SessionGrouper, SessionInfo, ClipInfo, PhotoInfo
from scribe_core.media_separator import MediaSeparator, SeparationPreview
from scribe_core.manifest import ManifestManager, EventDetails

class Stage2Tab(ttk.Frame):
    """Stage 2 Tab UI and Event Handler."""

    def __init__(self, parent, status_callback=None):
        super().__init__(parent, padding=10)
        self.status_callback = status_callback or (lambda msg: None)

        self.time_parser = TimeParser()
        self.clip_probe = ClipProbe()
        self.separator = MediaSeparator()
        self.grouper = SessionGrouper()

        # State
        self.current_preview: SeparationPreview = None
        self.sessions: list[SessionInfo] = []
        self.photos: list[PhotoInfo] = []
        self.unassigned_photos: list[PhotoInfo] = []
        self.clips: list[ClipInfo] = []

        self._build_ui()

    def _build_ui(self):
        # Master scrollable canvas or paned window
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Upper frame: Event Selection & Metadata Form
        upper_frame = ttk.LabelFrame(paned, text=" 1. Event Setup & Ingest Path ", padding=10)
        paned.add(upper_frame, weight=1)

        # Event folder selection
        folder_row = ttk.Frame(upper_frame)
        folder_row.pack(fill=tk.X, pady=4)
        ttk.Label(folder_row, text="Event Folder:", width=14, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.event_dir_var = tk.StringVar(value=r"D:\Events\17.ElevateUai_cybersecurity")
        self.event_entry = ttk.Entry(folder_row, textvariable=self.event_dir_var)
        self.event_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(folder_row, text="Browse...", command=self._browse_event_dir).pack(side=tk.LEFT, padx=2)
        ttk.Button(folder_row, text="Scan Events in D:\\Events", command=self._scan_events_dialog).pack(side=tk.LEFT, padx=2)

        # Event Details Form Grid
        form_frame = ttk.Frame(upper_frame)
        form_frame.pack(fill=tk.X, pady=6)

        # Row 1: Event Name & Date
        ttk.Label(form_frame, text="Event Name:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.event_name_var = tk.StringVar(value="CYBER & BEYOND 2026")
        ttk.Entry(form_frame, textvariable=self.event_name_var, width=32).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(form_frame, text="Date:").grid(row=0, column=2, sticky=tk.W, padx=(15, 0), pady=2)
        self.event_date_var = tk.StringVar(value="5 September 2026")
        ttk.Entry(form_frame, textvariable=self.event_date_var, width=20).grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        # Row 2: Organiser & Venue
        ttk.Label(form_frame, text="Organiser:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.organiser_var = tk.StringVar(value="elevateU.ai in collaboration with Quality Thought")
        ttk.Entry(form_frame, textvariable=self.organiser_var, width=32).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(form_frame, text="Venue:").grid(row=1, column=2, sticky=tk.W, padx=(15, 0), pady=2)
        self.venue_var = tk.StringVar(value="N-Heights building 5th floor, Hyderabad")
        ttk.Entry(form_frame, textvariable=self.venue_var, width=32).grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)

        # Row 3: Sponsors (optional) & Speaker Mode
        ttk.Label(form_frame, text="Sponsors (opt):").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.sponsors_var = tk.StringVar(value="")
        ttk.Entry(form_frame, textvariable=self.sponsors_var, width=32).grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(form_frame, text="Speaker Mode:").grid(row=2, column=2, sticky=tk.W, padx=(15, 0), pady=2)
        self.speaker_mode_var = tk.StringVar(value="single")
        mode_frame = ttk.Frame(form_frame)
        mode_frame.grid(row=2, column=3, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(mode_frame, text="Single Speaker", variable=self.speaker_mode_var, value="single").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(mode_frame, text="Panel Discussion", variable=self.speaker_mode_var, value="panel").pack(side=tk.LEFT, padx=4)

        # Warnings label
        self.warning_label = ttk.Label(upper_frame, text="", foreground="#d9534f", font=("Segoe UI", 9, "italic"))
        self.warning_label.pack(anchor=tk.W, pady=2)

        # Separation Actions Row
        sep_actions = ttk.Frame(upper_frame)
        sep_actions.pack(fill=tk.X, pady=6)
        self.btn_dry_run = ttk.Button(sep_actions, text="Dry-Run Media Separation", command=self._run_dry_run)
        self.btn_dry_run.pack(side=tk.LEFT, padx=3)

        self.btn_confirm_move = ttk.Button(sep_actions, text="Confirm & Move Files", state=tk.DISABLED, command=self._confirm_separation)
        self.btn_confirm_move.pack(side=tk.LEFT, padx=3)

        self.btn_undo = ttk.Button(sep_actions, text="Undo Last Separation", command=self._undo_separation)
        self.btn_undo.pack(side=tk.LEFT, padx=3)

        self.sep_status_var = tk.StringVar(value="Ready. Check paths and run dry-run preview before moving.")
        ttk.Label(sep_actions, textvariable=self.sep_status_var, foreground="#0275d8").pack(side=tk.LEFT, padx=10)

        # Middle frame: Sessions & Photo Assignment
        middle_frame = ttk.LabelFrame(paned, text=" 2. Session Auto-Grouping & Photo Alignment ", padding=10)
        paned.add(middle_frame, weight=3)

        # Controls row
        ctrl_row = ttk.Frame(middle_frame)
        ctrl_row.pack(fill=tk.X, pady=4)

        ttk.Label(ctrl_row, text="Gap Threshold (min):").pack(side=tk.LEFT)
        self.gap_min_var = tk.DoubleVar(value=10.0)
        ttk.Spinbox(ctrl_row, from_=1.0, to=120.0, increment=1.0, textvariable=self.gap_min_var, width=6).pack(side=tk.LEFT, padx=5)

        ttk.Label(ctrl_row, text="Photo Grace Margin (min):").pack(side=tk.LEFT, padx=(15, 0))
        self.grace_min_var = tk.DoubleVar(value=3.0)
        ttk.Spinbox(ctrl_row, from_=0.0, to=60.0, increment=0.5, textvariable=self.grace_min_var, width=6).pack(side=tk.LEFT, padx=5)

        self.btn_detect = ttk.Button(ctrl_row, text="Detect Sessions & Assign Photos", command=self._run_session_detection)
        self.btn_detect.pack(side=tk.LEFT, padx=15)

        self.btn_save_manifest = ttk.Button(ctrl_row, text="Save Manifest (manifest.json)", command=self._save_manifest)
        self.btn_save_manifest.pack(side=tk.RIGHT, padx=5)

        # Tables PanedWindow (Sessions table on left, Photos on right)
        tables_pane = ttk.PanedWindow(middle_frame, orient=tk.HORIZONTAL)
        tables_pane.pack(fill=tk.BOTH, expand=True, pady=4)

        # Sessions Table Frame
        sess_frame = ttk.Frame(tables_pane)
        tables_pane.add(sess_frame, weight=1)
        ttk.Label(sess_frame, text="Sessions (Double-click speaker name to rename):", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)

        self.sess_tree = ttk.Treeview(
            sess_frame,
            columns=("id", "speaker", "clips", "photos", "start", "end"),
            show="headings",
            selectmode="browse",
            height=8
        )
        self.sess_tree.heading("id", text="#")
        self.sess_tree.heading("speaker", text="Speaker Name")
        self.sess_tree.heading("clips", text="Clips")
        self.sess_tree.heading("photos", text="Photos")
        self.sess_tree.heading("start", text="Start Time")
        self.sess_tree.heading("end", text="End Time")

        self.sess_tree.column("id", width=35, anchor=tk.CENTER)
        self.sess_tree.column("speaker", width=120)
        self.sess_tree.column("clips", width=50, anchor=tk.CENTER)
        self.sess_tree.column("photos", width=55, anchor=tk.CENTER)
        self.sess_tree.column("start", width=80, anchor=tk.CENTER)
        self.sess_tree.column("end", width=80, anchor=tk.CENTER)

        sess_scroll = ttk.Scrollbar(sess_frame, orient=tk.VERTICAL, command=self.sess_tree.yview)
        self.sess_tree.configure(yscrollcommand=sess_scroll.set)
        self.sess_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sess_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sess_tree.bind("<Double-1>", self._on_session_double_click)

        # Photos Table Frame
        photo_frame = ttk.Frame(tables_pane)
        tables_pane.add(photo_frame, weight=1)
        ttk.Label(photo_frame, text="Photos & Session Assignment:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)

        self.photo_tree = ttk.Treeview(
            photo_frame,
            columns=("name", "time", "assigned", "status"),
            show="headings",
            selectmode="browse",
            height=8
        )
        self.photo_tree.heading("name", text="Photo Filename")
        self.photo_tree.heading("time", text="Timestamp (IST)")
        self.photo_tree.heading("assigned", text="Assigned Speaker")
        self.photo_tree.heading("status", text="Status")

        self.photo_tree.column("name", width=160)
        self.photo_tree.column("time", width=95, anchor=tk.CENTER)
        self.photo_tree.column("assigned", width=110)
        self.photo_tree.column("status", width=80, anchor=tk.CENTER)

        photo_scroll = ttk.Scrollbar(photo_frame, orient=tk.VERTICAL, command=self.photo_tree.yview)
        self.photo_tree.configure(yscrollcommand=photo_scroll.set)
        self.photo_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        photo_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # --- Folder helpers ---
    def _browse_event_dir(self):
        d = filedialog.askdirectory(initialdir=r"D:\Events", title="Select Event Folder")
        if d:
            self.event_dir_var.set(d)

    def _scan_events_dialog(self):
        events_root = r"D:\Events"
        if not os.path.exists(events_root):
            messagebox.showerror("Error", f"Directory {events_root} does not exist.")
            return

        items = [d for d in os.listdir(events_root) if os.path.isdir(os.path.join(events_root, d))]
        if not items:
            messagebox.showinfo("Scan Events", "No event directories found in D:\\Events.")
            return

        # Simple picker dialog
        dialog = tk.Toplevel(self)
        dialog.title("Select Event")
        dialog.geometry("400x350")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="Available Events in D:\\Events:", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=8)
        lb = tk.Listbox(dialog, selectmode=tk.SINGLE)
        for item in sorted(items):
            lb.insert(tk.END, item)
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        def select():
            sel = lb.curselection()
            if sel:
                event_name = lb.get(sel[0])
                self.event_dir_var.set(os.path.join(events_root, event_name))
                dialog.destroy()

        btn_box = ttk.Frame(dialog)
        btn_box.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(btn_box, text="Select", command=select).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_box, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

    def _get_target_paths(self):
        event_dir = self.event_dir_var.get().strip()
        data_dir = os.path.join(event_dir, "data")
        scribe_dir = os.path.join(event_dir, "scribe")
        return event_dir, data_dir, scribe_dir

    # --- Separation Operations ---
    def _run_dry_run(self):
        event_dir, data_dir, _ = self._get_target_paths()
        if not os.path.exists(data_dir):
            # Check if there is an alternate unseparated folder
            messagebox.showwarning(
                "Data Directory Missing",
                f"Data directory '{data_dir}' not found.\n"
                f"Session Scribe expects input media in:\n{data_dir}"
            )
            return

        try:
            self.current_preview = self.separator.dry_run(data_dir)
            report = (
                f"DRY-RUN PREVIEW FOR: {data_dir}\n"
                f"{'-'*50}\n"
                f"• Videos to move into Videos/ : {self.current_preview.video_count}\n"
                f"• Photos to move into Photos/ : {self.current_preview.photo_count}\n"
                f"• Unknown files (stay in data/): {self.current_preview.unknown_count}\n"
                f"• Potential Name Collisions   : {self.current_preview.collision_count}\n"
                f"• Files already separated     : {self.current_preview.already_separated_count}\n"
            )
            if self.current_preview.unknown_files:
                report += f"\nUnknown files staying in place:\n  " + "\n  ".join(self.current_preview.unknown_files[:10])
                if len(self.current_preview.unknown_files) > 10:
                    report += f"\n  ... and {len(self.current_preview.unknown_files) - 10} more."

            if self.current_preview.collisions:
                report += f"\n\nCollisions handled safely (both kept):\n"
                for c in self.current_preview.collisions:
                    report += f"  {c['original']} -> {c['target_subfolder']}/{c['resolution']}\n"

            report += "\nNothing has moved on disk. Click 'Confirm & Move Files' to proceed."
            messagebox.showinfo("Dry-Run Separation Preview", report)

            self.btn_confirm_move.configure(state=tk.NORMAL)
            self.sep_status_var.set(
                f"Dry-run OK: {self.current_preview.video_count} videos, "
                f"{self.current_preview.photo_count} photos, {self.current_preview.collision_count} collisions."
            )
        except Exception as e:
            messagebox.showerror("Dry Run Error", str(e))

    def _confirm_separation(self):
        event_dir, data_dir, scribe_dir = self._get_target_paths()
        if not self.current_preview:
            return

        confirm = messagebox.askyesno(
            "Confirm Move",
            f"Move {self.current_preview.video_count} videos and {self.current_preview.photo_count} photos?\n"
            f"This is an instant same-drive move that preserves file timestamps.\n"
            f"A move log will be created in {scribe_dir}\\move_log.json for undo."
        )
        if not confirm:
            return

        try:
            res = self.separator.separate(data_dir, scribe_dir)
            if res.success:
                messagebox.showinfo("Separation Success", res.message)
                self.sep_status_var.set(f"Separation complete and verified! Move log written.")
                self.btn_confirm_move.configure(state=tk.DISABLED)
            else:
                messagebox.showerror("Verification Failed", res.message)
        except Exception as e:
            messagebox.showerror("Separation Error", str(e))

    def _undo_separation(self):
        _, _, scribe_dir = self._get_target_paths()
        move_log = os.path.join(scribe_dir, "move_log.json")
        if not os.path.exists(move_log):
            messagebox.showwarning("Undo", f"No move_log.json found in {scribe_dir}.")
            return

        confirm = messagebox.askyesno("Undo Separation", "Restore all moved media back to their original locations?")
        if not confirm:
            return

        res = self.separator.undo(scribe_dir)
        if res.success:
            messagebox.showinfo("Undo Complete", res.message)
            self.sep_status_var.set(res.message)
            self.btn_confirm_move.configure(state=tk.DISABLED)
        else:
            messagebox.showerror("Undo Error", res.message)

    # --- Session & Photo Detection ---
    def _run_session_detection(self):
        event_dir = self.event_dir_var.get().strip()
        data_dir = os.path.join(event_dir, "data")
        
        # Check standard data/Videos and data/Photos first, fallback to alternate subfolders if present
        videos_dir = os.path.join(data_dir, "Videos")
        photos_dir = os.path.join(data_dir, "Photos")

        if not os.path.exists(videos_dir):
            # Check Photos-1-001/Videos or similar legacy folders
            legacy_videos = [
                os.path.join(event_dir, "Photos-1-001", "Videos"),
                os.path.join(event_dir, "Photos-1-001 (1)", "Videos")
            ]
            for lv in legacy_videos:
                if os.path.exists(lv):
                    videos_dir = lv
                    break

        if not os.path.exists(photos_dir):
            legacy_photos = [
                os.path.join(event_dir, "Photos-1-001", "Photos"),
                os.path.join(event_dir, "Photos-1-001 (1)", "Photos")
            ]
            for lp in legacy_photos:
                if os.path.exists(lp):
                    photos_dir = lp
                    break

        if not os.path.exists(videos_dir):
            messagebox.showerror("Error", f"No Videos directory found in {data_dir} or legacy event folders.")
            return

        self.btn_detect.configure(state=tk.DISABLED)
        self.status_callback("Scanning video clips and reading durations with ffprobe...")

        # Run in thread so GUI remains responsive
        threading.Thread(
            target=self._detect_thread_worker,
            args=(videos_dir, photos_dir),
            daemon=True
        ).start()

    def _detect_thread_worker(self, videos_dir: str, photos_dir: str):
        try:
            video_files = [f for f in os.listdir(videos_dir) if self.separator.classify_extension(f) == "video"]
            clips = []
            for f in video_files:
                p = os.path.join(videos_dir, f)
                dt, src = self.time_parser.get_timestamp(p, is_photo=False)
                dur = self.clip_probe.get_duration(p)
                end_dt = dt + timedelta(seconds=dur)
                clips.append(ClipInfo(
                    filename=f,
                    filepath=p,
                    start_time=dt,
                    duration=dur,
                    end_time=end_dt,
                    time_source=src
                ))

            grouper = SessionGrouper(
                gap_threshold_seconds=self.gap_min_var.get() * 60.0,
                photo_grace_seconds=self.grace_min_var.get() * 60.0
            )
            sessions = grouper.group_clips(clips)

            photos = []
            if os.path.exists(photos_dir):
                photo_files = [f for f in os.listdir(photos_dir) if self.separator.classify_extension(f) == "photo"]
                for f in photo_files:
                    p = os.path.join(photos_dir, f)
                    dt, src = self.time_parser.get_timestamp(p, is_photo=True)
                    photos.append(PhotoInfo(
                        filename=f,
                        filepath=p,
                        timestamp=dt,
                        time_source=src
                    ))

            sessions, unassigned = grouper.assign_photos(photos, sessions)

            # Update GUI on main thread
            self.after(0, self._on_detection_complete, sessions, photos, unassigned, clips)
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Detection Failed", str(err)))
            self.after(0, lambda: self.btn_detect.configure(state=tk.NORMAL))

    def _on_detection_complete(self, sessions, photos, unassigned, clips):
        self.sessions = sessions
        self.photos = photos
        self.unassigned_photos = unassigned
        self.clips = clips
        self.btn_detect.configure(state=tk.NORMAL)

        # Clear trees
        for item in self.sess_tree.get_children():
            self.sess_tree.delete(item)
        for item in self.photo_tree.get_children():
            self.photo_tree.delete(item)

        # Populate sessions tree
        for s in self.sessions:
            st = s.start_time.strftime("%H:%M:%S") if s.start_time else "--"
            et = s.end_time.strftime("%H:%M:%S") if s.end_time else "--"
            self.sess_tree.insert(
                "",
                tk.END,
                iid=str(s.session_id),
                values=(s.session_id, s.speaker_name, len(s.clips), len(s.photos), st, et)
            )

        # Populate photos tree
        for p in self.photos:
            t = p.timestamp.strftime("%H:%M:%S") if p.timestamp else "--"
            spk = p.assigned_speaker if p.assigned_speaker else "None"
            self.photo_tree.insert(
                "",
                tk.END,
                values=(p.filename, t, spk, p.assignment_status)
            )

        msg = (
            f"Detected {len(self.sessions)} sessions across {len(self.clips)} clips. "
            f"Assigned {len(self.photos) - len(self.unassigned_photos)}/{len(self.photos)} photos "
            f"({len(self.unassigned_photos)} unassigned)."
        )
        self.status_callback(msg)
        messagebox.showinfo("Session Grouping Complete", msg)

    def _on_session_double_click(self, event):
        """Allows in-place editing of speaker name in session table."""
        item_id = self.sess_tree.focus()
        if not item_id:
            return

        curr_values = self.sess_tree.item(item_id, "values")
        session_id = int(curr_values[0])
        old_name = curr_values[1]

        # Simple input popup
        prompt_win = tk.Toplevel(self)
        prompt_win.title(f"Rename Session {session_id}")
        prompt_win.geometry("320x130")
        prompt_win.transient(self)
        prompt_win.grab_set()

        ttk.Label(prompt_win, text=f"Enter Speaker Name for Session {session_id}:").pack(pady=(12, 4), padx=10)
        entry_var = tk.StringVar(value=old_name)
        entry = ttk.Entry(prompt_win, textvariable=entry_var, width=28)
        entry.pack(pady=4, padx=10)
        entry.focus()

        def apply_name():
            new_name = entry_var.get().strip() or f"Speaker {session_id}"
            for s in self.sessions:
                if s.session_id == session_id:
                    s.speaker_name = new_name
                    # Update photos assigned to this session
                    for p in s.photos:
                        p.assigned_speaker = new_name
            
            # Refresh session tree row
            st = curr_values[4]
            et = curr_values[5]
            self.sess_tree.item(item_id, values=(session_id, new_name, curr_values[2], curr_values[3], st, et))

            # Refresh photos tree
            for item in self.photo_tree.get_children():
                self.photo_tree.delete(item)
            for p in self.photos:
                t = p.timestamp.strftime("%H:%M:%S") if p.timestamp else "--"
                spk = p.assigned_speaker if p.assigned_speaker else "None"
                self.photo_tree.insert("", tk.END, values=(p.filename, t, spk, p.assignment_status))

            prompt_win.destroy()

        btn_box = ttk.Frame(prompt_win)
        btn_box.pack(fill=tk.X, pady=8, padx=10)
        ttk.Button(btn_box, text="Save", command=apply_name).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_box, text="Cancel", command=prompt_win.destroy).pack(side=tk.RIGHT)

    # --- Manifest Generation ---
    def _save_manifest(self):
        _, _, scribe_dir = self._get_target_paths()

        event = EventDetails(
            event_name=self.event_name_var.get().strip(),
            date=self.event_date_var.get().strip(),
            organiser=self.organiser_var.get().strip(),
            venue=self.venue_var.get().strip(),
            sponsors=self.sponsors_var.get().strip(),
            speaker_mode=self.speaker_mode_var.get()
        )

        warnings = event.validate()
        if warnings:
            self.warning_label.configure(text="WARNING: " + " | ".join(warnings))
            confirm = messagebox.askyesno(
                "Missing Fields Warning",
                "Validation Warnings:\n" + "\n".join(f"• {w}" for w in warnings) +
                "\n\nDo you want to proceed saving manifest anyway?"
            )
            if not confirm:
                return
        else:
            self.warning_label.configure(text="")

        if not self.sessions:
            messagebox.showwarning("No Sessions", "Please run 'Detect Sessions & Assign Photos' first.")
            return

        settings = {
            "session_gap_threshold_seconds": self.gap_min_var.get() * 60.0,
            "photo_grace_seconds": self.grace_min_var.get() * 60.0
        }

        try:
            path, warns = ManifestManager.save_manifest(
                scribe_dir=scribe_dir,
                event=event,
                sessions=self.sessions,
                unassigned_photos=self.unassigned_photos,
                settings_used=settings
            )
            msg = f"Manifest successfully saved to:\n{path}\n\nSummary:\n"
            msg += f"• Event: {event.event_name}\n"
            msg += f"• Sessions: {len(self.sessions)}\n"
            msg += f"• Clips: {sum(len(s.clips) for s in self.sessions)}\n"
            msg += f"• Assigned Photos: {sum(len(s.photos) for s in self.sessions)}\n"
            msg += f"• Unassigned Photos: {len(self.unassigned_photos)}"
            messagebox.showinfo("Manifest Saved", msg)
            self.status_callback(f"Manifest saved to {path}")
        except Exception as e:
            messagebox.showerror("Error Saving Manifest", str(e))
