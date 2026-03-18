"""Full customtkinter UI: main window, sidebar, tabs, dialogs."""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import logging
import os
from datetime import datetime
from typing import Optional

from database import (
    get_connection, init_db, get_all_schedules, get_schedule,
    create_schedule, update_schedule, delete_schedule, toggle_schedule,
    get_runs, get_run, get_prompt_results,
    get_setting, set_setting, purge_old_runs,
)
from scheduler_engine import SchedulerEngine

log = logging.getLogger("ui")

# ── Colors ────────────────────────────────────────────────────────────────
STATUS_COLORS = {
    "success": "#2fa572",
    "failure": "#e74c3c",
    "partial_failure": "#f39c12",
    "running": "#3498db",
    "error": "#e74c3c",
    "pending": "#95a5a6",
    "skipped": "#95a5a6",
}
STATUS_LABELS = {
    "success": "Success",
    "failure": "Failed",
    "partial_failure": "Partial",
    "running": "Running...",
    "error": "Error",
    "pending": "Pending",
    "skipped": "Skipped",
}
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Terminal mode options
TERMINAL_MODE_VALUES = ["headless", "visible", "interactive"]
TERMINAL_MODE_LABELS = ["Headless", "Visible", "Interactive"]
TERMINAL_MODE_DESCRIPTIONS = {
    "headless": "Runs completely hidden. Full JSON output captured. No user interaction.",
    "visible": "Opens a console window to watch Claude work (read-only). Full JSON output captured.",
    "interactive": "Opens Claude TUI for full interaction. Only first prompt is used. Limited output capture.",
}

# CLI Provider options
CLI_PROVIDER_VALUES = ["claude", "copilot"]
CLI_PROVIDER_LABELS = ["Claude Code", "GitHub Copilot"]
CLI_PROVIDER_DESCRIPTIONS = {
    "claude": "Uses Claude Code CLI. Supports JSON output, cost tracking, and session resume.",
    "copilot": "Uses GitHub Copilot CLI. Text output only, no cost tracking. Uses --continue for chaining.",
}


class App(ctk.CTk):
    def __init__(self, db_path: str, engine: SchedulerEngine, start_hidden: bool = False):
        super().__init__()
        self.db_path = db_path
        self.engine = engine
        self.engine.ui_callback = self._engine_event

        self.title("Claude Code Scheduler")
        self.geometry("1100x700")
        self.minsize(900, 550)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._current_tab = "schedules"
        self._build_layout()
        self._show_tab("schedules")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Periodic UI refresh
        self._tick()

        if start_hidden:
            self.withdraw()

    # ── Layout ────────────────────────────────────────────────────────

    def _build_layout(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo = ctk.CTkLabel(self.sidebar, text="Claude Scheduler",
                            font=ctk.CTkFont(size=16, weight="bold"))
        logo.pack(pady=(20, 10), padx=10)

        self.btn_schedules = ctk.CTkButton(
            self.sidebar, text="Schedules", command=lambda: self._show_tab("schedules"))
        self.btn_schedules.pack(pady=4, padx=10, fill="x")

        self.btn_history = ctk.CTkButton(
            self.sidebar, text="History", command=lambda: self._show_tab("history"))
        self.btn_history.pack(pady=4, padx=10, fill="x")

        self.btn_settings = ctk.CTkButton(
            self.sidebar, text="Settings", command=lambda: self._show_tab("settings"))
        self.btn_settings.pack(pady=4, padx=10, fill="x")

        # Spacer
        ctk.CTkLabel(self.sidebar, text="").pack(expand=True)

        # Engine status
        self.lbl_engine = ctk.CTkLabel(self.sidebar, text="Engine: Running",
                                       font=ctk.CTkFont(size=11), text_color="#2fa572")
        self.lbl_engine.pack(pady=(0, 2), padx=10)

        self.lbl_next_run = ctk.CTkLabel(self.sidebar, text="Next: --",
                                         font=ctk.CTkFont(size=11), text_color="#95a5a6")
        self.lbl_next_run.pack(pady=(0, 15), padx=10)

        # Content area
        self.content = ctk.CTkFrame(self, corner_radius=0)
        self.content.pack(side="right", fill="both", expand=True)

    def _show_tab(self, tab: str):
        self._current_tab = tab
        for w in self.content.winfo_children():
            w.destroy()

        # Highlight active sidebar button
        default_color = ctk.ThemeManager.theme["CTkButton"]["fg_color"]
        for btn, name in [(self.btn_schedules, "schedules"),
                          (self.btn_history, "history"),
                          (self.btn_settings, "settings")]:
            if name == tab:
                btn.configure(fg_color="#1f6aa5")
            else:
                btn.configure(fg_color=default_color)

        if tab == "schedules":
            self._build_schedules_tab()
        elif tab == "history":
            self._build_history_tab()
        elif tab == "settings":
            self._build_settings_tab()

    # ── Schedules Tab ─────────────────────────────────────────────────

    def _build_schedules_tab(self):
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(header, text="Schedules",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

        ctk.CTkButton(header, text="+ New Schedule", width=130,
                      command=self._open_schedule_dialog).pack(side="right", padx=5)

        # Scrollable card list
        self.schedule_scroll = ctk.CTkScrollableFrame(self.content)
        self.schedule_scroll.pack(fill="both", expand=True, padx=15, pady=10)

        self._refresh_schedule_list()

    def _refresh_schedule_list(self):
        if not hasattr(self, "schedule_scroll") or not self.schedule_scroll.winfo_exists():
            return
        for w in self.schedule_scroll.winfo_children():
            w.destroy()

        conn = get_connection(self.db_path)
        schedules = get_all_schedules(conn)
        conn.close()

        if not schedules:
            ctk.CTkLabel(self.schedule_scroll,
                         text="No schedules yet. Click '+ New Schedule' to create one.",
                         text_color="#95a5a6").pack(pady=40)
            return

        for sched in schedules:
            self._build_schedule_card(sched)

    def _build_schedule_card(self, sched: dict):
        card = ctk.CTkFrame(self.schedule_scroll, corner_radius=8)
        card.pack(fill="x", pady=4)

        # Top row: name, status badge, toggle
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))

        ctk.CTkLabel(top, text=sched["name"],
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")

        # Status badge
        status = sched.get("last_status")
        if self.engine.is_schedule_running(sched["id"]):
            status = "running"
        if status:
            color = STATUS_COLORS.get(status, "#95a5a6")
            label = STATUS_LABELS.get(status, status)
            badge = ctk.CTkLabel(top, text=f" {label} ", font=ctk.CTkFont(size=11),
                                 fg_color=color, corner_radius=4, text_color="white")
            badge.pack(side="left", padx=10)

        # Enable toggle
        var = ctk.BooleanVar(value=bool(sched["enabled"]))
        switch = ctk.CTkSwitch(
            top, text="Enabled", variable=var, width=40,
            command=lambda sid=sched["id"], v=var: self._toggle_schedule(sid, v))
        switch.pack(side="right")

        # Detail row
        detail = ctk.CTkFrame(card, fg_color="transparent")
        detail.pack(fill="x", padx=12, pady=(0, 4))

        freq_text = sched["frequency"].capitalize()
        if sched["frequency"] == "weekly" and sched.get("day_of_week") is not None:
            freq_text += f" ({DAYS_OF_WEEK[sched['day_of_week']]})"
        freq_text += f" at {sched['scheduled_time']}"
        prompts_text = f"{len(sched.get('prompts', []))} prompt(s)"

        # Terminal mode label
        mode_val = sched.get("terminal_mode", "headless")
        if mode_val in TERMINAL_MODE_VALUES:
            mode_label = TERMINAL_MODE_LABELS[TERMINAL_MODE_VALUES.index(mode_val)]
        else:
            mode_label = "Headless"

        # CLI provider label
        provider_val = sched.get("cli_provider", "claude")
        if provider_val in CLI_PROVIDER_VALUES:
            provider_label = CLI_PROVIDER_LABELS[CLI_PROVIDER_VALUES.index(provider_val)]
        else:
            provider_label = "Claude Code"

        ctk.CTkLabel(detail, text=f"{freq_text}  |  {prompts_text}  |  {mode_label}  |  {provider_label}  |  {sched['working_dir']}",
                     font=ctk.CTkFont(size=11), text_color="#95a5a6").pack(side="left")

        # Buttons
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkButton(btn_frame, text="Run Now", width=80, height=28, fg_color="#3498db",
                      command=lambda sid=sched["id"]: self._run_now(sid)).pack(side="left", padx=(0, 5))
        ctk.CTkButton(btn_frame, text="Edit", width=60, height=28,
                      command=lambda sid=sched["id"]: self._open_schedule_dialog(sid)).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Delete", width=60, height=28, fg_color="#e74c3c",
                      hover_color="#c0392b",
                      command=lambda sid=sched["id"]: self._delete_schedule(sid)).pack(side="left", padx=5)

    def _toggle_schedule(self, schedule_id: int, var: ctk.BooleanVar):
        conn = get_connection(self.db_path)
        toggle_schedule(conn, schedule_id, var.get())
        conn.close()

    def _run_now(self, schedule_id: int):
        self.engine.execute_now(schedule_id)
        self.after(500, lambda: self._show_tab("schedules"))

    def _delete_schedule(self, schedule_id: int):
        if messagebox.askyesno("Delete Schedule", "Are you sure you want to delete this schedule?"):
            conn = get_connection(self.db_path)
            delete_schedule(conn, schedule_id)
            conn.close()
            self._refresh_schedule_list()

    # ── Schedule Dialog ───────────────────────────────────────────────

    def _open_schedule_dialog(self, schedule_id: Optional[int] = None):
        ScheduleDialog(self, self.db_path, schedule_id, on_save=self._refresh_schedule_list)

    # ── History Tab ───────────────────────────────────────────────────

    def _build_history_tab(self):
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(header, text="Run History",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

        # Filter
        self._history_filter = ctk.CTkComboBox(
            header, values=["All Schedules"], width=200,
            command=self._on_history_filter)
        self._history_filter.pack(side="right")

        conn = get_connection(self.db_path)
        schedules = get_all_schedules(conn)
        conn.close()
        filter_vals = ["All Schedules"] + [s["name"] for s in schedules]
        self._history_filter.configure(values=filter_vals)
        self._history_schedules_map = {s["name"]: s["id"] for s in schedules}

        self.history_scroll = ctk.CTkScrollableFrame(self.content)
        self.history_scroll.pack(fill="both", expand=True, padx=15, pady=10)

        self._refresh_history_list()

    def _on_history_filter(self, value: str):
        self._refresh_history_list()

    def _refresh_history_list(self):
        if not hasattr(self, "history_scroll") or not self.history_scroll.winfo_exists():
            return
        for w in self.history_scroll.winfo_children():
            w.destroy()

        filter_val = self._history_filter.get() if hasattr(self, "_history_filter") else "All Schedules"
        schedule_id = self._history_schedules_map.get(filter_val) if filter_val != "All Schedules" else None

        conn = get_connection(self.db_path)
        runs = get_runs(conn, schedule_id=schedule_id, limit=200)
        conn.close()

        if not runs:
            ctk.CTkLabel(self.history_scroll,
                         text="No runs yet.",
                         text_color="#95a5a6").pack(pady=40)
            return

        for run in runs:
            self._build_run_row(run)

    def _build_run_row(self, run: dict):
        row = ctk.CTkFrame(self.history_scroll, corner_radius=6, height=45)
        row.pack(fill="x", pady=2)
        row.pack_propagate(False)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10)

        # Status badge
        status = run["status"]
        color = STATUS_COLORS.get(status, "#95a5a6")
        label = STATUS_LABELS.get(status, status)
        badge = ctk.CTkLabel(inner, text=f" {label} ", font=ctk.CTkFont(size=11),
                             fg_color=color, corner_radius=4, text_color="white", width=70)
        badge.pack(side="left", pady=8)

        # Schedule name
        ctk.CTkLabel(inner, text=run.get("schedule_name", "?"),
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=10)

        # Provider
        run_provider = run.get("cli_provider", "claude")
        if run_provider in CLI_PROVIDER_VALUES:
            provider_display = CLI_PROVIDER_LABELS[CLI_PROVIDER_VALUES.index(run_provider)]
        else:
            provider_display = "Claude Code"
        ctk.CTkLabel(inner, text=provider_display, font=ctk.CTkFont(size=11),
                     text_color="#7f8c8d").pack(side="left", padx=10)

        # Time
        started = run.get("started_at", "")
        ctk.CTkLabel(inner, text=started, font=ctk.CTkFont(size=11),
                     text_color="#95a5a6").pack(side="left", padx=10)

        # Cost
        if run_provider == "copilot":
            ctk.CTkLabel(inner, text="N/A", font=ctk.CTkFont(size=11),
                         text_color="#95a5a6").pack(side="left", padx=10)
        else:
            cost = run.get("total_cost_usd", 0) or 0
            if cost > 0:
                ctk.CTkLabel(inner, text=f"${cost:.4f}", font=ctk.CTkFont(size=11),
                             text_color="#95a5a6").pack(side="left", padx=10)

        # View details
        ctk.CTkButton(inner, text="Details", width=60, height=26,
                      command=lambda rid=run["id"]: self._open_run_detail(rid)).pack(side="right", pady=8)

    def _open_run_detail(self, run_id: int):
        RunDetailDialog(self, self.db_path, run_id)

    # ── Settings Tab ──────────────────────────────────────────────────

    def _build_settings_tab(self):
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(header, text="Settings",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

        form = ctk.CTkScrollableFrame(self.content)
        form.pack(fill="both", expand=True, padx=15, pady=10)

        conn = get_connection(self.db_path)

        # ── Claude Code CLI section ──
        ctk.CTkLabel(form, text="Claude Code CLI",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 4))

        ctk.CTkLabel(form, text="Executable",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 2))
        self._settings_claude_exe = ctk.CTkEntry(form, width=400)
        self._settings_claude_exe.insert(0, get_setting(conn, "claude_executable", "claude"))
        self._settings_claude_exe.pack(anchor="w")

        self._settings_skip_perms = ctk.CTkSwitch(
            form, text="Use --dangerously-skip-permissions (allows full autonomous execution)")
        if get_setting(conn, "dangerously_skip_permissions", "true") == "true":
            self._settings_skip_perms.select()
        self._settings_skip_perms.pack(anchor="w", pady=(10, 4))

        # ── GitHub Copilot CLI section ──
        ctk.CTkLabel(form, text="GitHub Copilot CLI",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(20, 4))

        ctk.CTkLabel(form, text="Executable",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 2))
        self._settings_copilot_exe = ctk.CTkEntry(form, width=400)
        self._settings_copilot_exe.insert(0, get_setting(conn, "copilot_executable", "copilot"))
        self._settings_copilot_exe.pack(anchor="w")

        self._settings_copilot_yolo = ctk.CTkSwitch(
            form, text="Use --yolo (skip permission prompts)")
        if get_setting(conn, "copilot_skip_permissions", "true") == "true":
            self._settings_copilot_yolo.select()
        self._settings_copilot_yolo.pack(anchor="w", pady=(10, 4))

        self._settings_copilot_autopilot = ctk.CTkSwitch(
            form, text="Use --autopilot (autonomous execution)")
        if get_setting(conn, "copilot_autopilot", "true") == "true":
            self._settings_copilot_autopilot.select()
        self._settings_copilot_autopilot.pack(anchor="w", pady=(4, 4))

        ctk.CTkLabel(form, text="Max Autopilot Continues",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(10, 2))
        self._settings_copilot_max_continues = ctk.CTkEntry(form, width=100)
        self._settings_copilot_max_continues.insert(0, get_setting(conn, "copilot_max_continues", "50"))
        self._settings_copilot_max_continues.pack(anchor="w")

        # ── Scheduler section ──
        ctk.CTkLabel(form, text="Scheduler",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(20, 4))

        # Check interval
        ctk.CTkLabel(form, text="Check Interval (seconds)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(15, 2))
        self._settings_interval = ctk.CTkEntry(form, width=100)
        self._settings_interval.insert(0, get_setting(conn, "check_interval_seconds", "30"))
        self._settings_interval.pack(anchor="w")

        # History retention
        ctk.CTkLabel(form, text="History Retention (days)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(15, 2))
        self._settings_retention = ctk.CTkEntry(form, width=100)
        self._settings_retention.insert(0, get_setting(conn, "history_retention_days", "90"))
        self._settings_retention.pack(anchor="w")

        # Run on Startup
        self._settings_startup = ctk.CTkSwitch(form, text="Run on Windows Startup")
        startup_val = get_setting(conn, "run_on_startup", "false") == "true"
        if startup_val:
            self._settings_startup.select()
        self._settings_startup.pack(anchor="w", pady=(15, 2))

        # Minimize to tray
        self._settings_tray = ctk.CTkSwitch(form, text="Minimize to System Tray")
        tray_val = get_setting(conn, "minimize_to_tray", "true") == "true"
        if tray_val:
            self._settings_tray.select()
        self._settings_tray.pack(anchor="w", pady=8)

        # Notifications
        self._settings_notify_success = ctk.CTkSwitch(form, text="Notify on Success")
        if get_setting(conn, "notify_on_success", "true") == "true":
            self._settings_notify_success.select()
        self._settings_notify_success.pack(anchor="w", pady=4)

        self._settings_notify_failure = ctk.CTkSwitch(form, text="Notify on Failure")
        if get_setting(conn, "notify_on_failure", "true") == "true":
            self._settings_notify_failure.select()
        self._settings_notify_failure.pack(anchor="w", pady=4)

        conn.close()

        # Save button
        ctk.CTkButton(form, text="Save Settings", width=140,
                      command=self._save_settings).pack(anchor="w", pady=(20, 10))

    def _save_settings(self):
        conn = get_connection(self.db_path)
        # Claude settings
        set_setting(conn, "claude_executable", self._settings_claude_exe.get().strip())
        set_setting(conn, "dangerously_skip_permissions", "true" if self._settings_skip_perms.get() else "false")
        # Copilot settings
        set_setting(conn, "copilot_executable", self._settings_copilot_exe.get().strip())
        set_setting(conn, "copilot_skip_permissions", "true" if self._settings_copilot_yolo.get() else "false")
        set_setting(conn, "copilot_autopilot", "true" if self._settings_copilot_autopilot.get() else "false")
        set_setting(conn, "copilot_max_continues", self._settings_copilot_max_continues.get().strip())
        # Scheduler settings
        set_setting(conn, "check_interval_seconds", self._settings_interval.get().strip())
        set_setting(conn, "history_retention_days", self._settings_retention.get().strip())
        set_setting(conn, "run_on_startup", "true" if self._settings_startup.get() else "false")
        set_setting(conn, "minimize_to_tray", "true" if self._settings_tray.get() else "false")
        set_setting(conn, "notify_on_success", "true" if self._settings_notify_success.get() else "false")
        set_setting(conn, "notify_on_failure", "true" if self._settings_notify_failure.get() else "false")

        # Handle startup registry
        startup = self._settings_startup.get()
        self._set_startup_registry(startup)

        # Purge old history
        try:
            days = int(self._settings_retention.get().strip())
            purge_old_runs(conn, days)
        except ValueError:
            pass

        conn.close()
        messagebox.showinfo("Settings", "Settings saved successfully.")

    def _set_startup_registry(self, enable: bool):
        try:
            import winreg
            import sys
            import os
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "ClaudeCodeScheduler"
            if enable:
                exe = sys.executable
                script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
                # Use pythonw to avoid console window
                pythonw = exe.replace("python.exe", "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = exe
                value = f'"{pythonw}" "{script}" --background'
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, value)
                winreg.CloseKey(key)
            else:
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                    winreg.DeleteValue(key, app_name)
                    winreg.CloseKey(key)
                except FileNotFoundError:
                    pass
        except Exception as e:
            log.warning("Failed to set startup registry: %s", e)

    # ── Engine events (called from background threads) ────────────────

    def _engine_event(self, event: str, **kwargs):
        """Thread-safe callback from the scheduler engine."""
        self.after(0, self._handle_engine_event, event, kwargs)

    def _handle_engine_event(self, event: str, kwargs: dict):
        if event in ("run_started", "prompt_started", "prompt_finished"):
            if self._current_tab == "schedules":
                self._refresh_schedule_list()
        elif event == "run_finished":
            if self._current_tab == "schedules":
                self._refresh_schedule_list()
            elif self._current_tab == "history":
                self._refresh_history_list()
            # Tray notification is handled by main.py

    # ── Periodic tick ─────────────────────────────────────────────────

    def _tick(self):
        # Update engine status
        if self.engine.running:
            if self.engine.paused:
                self.lbl_engine.configure(text="Engine: Paused", text_color="#f39c12")
            else:
                self.lbl_engine.configure(text="Engine: Running", text_color="#2fa572")
        else:
            self.lbl_engine.configure(text="Engine: Stopped", text_color="#e74c3c")

        # Update next run
        nxt = self.engine.get_next_run_info()
        self.lbl_next_run.configure(text=f"Next: {nxt}" if nxt else "Next: --")

        self.after(5000, self._tick)

    # ── Window close ──────────────────────────────────────────────────

    def _on_close(self):
        conn = get_connection(self.db_path)
        minimize = get_setting(conn, "minimize_to_tray", "true") == "true"
        conn.close()
        if minimize:
            self.withdraw()
        else:
            self._quit()

    def _quit(self):
        self.engine.stop()
        self.destroy()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self):
        self.engine.stop()
        try:
            self.destroy()
        except Exception:
            pass


# ── Schedule Dialog ───────────────────────────────────────────────────────

class ScheduleDialog(ctk.CTkToplevel):
    def __init__(self, parent, db_path: str, schedule_id: Optional[int] = None, on_save=None):
        super().__init__(parent)
        self.db_path = db_path
        self.schedule_id = schedule_id
        self.on_save = on_save
        self._prompts: list[str] = []

        self.title("Edit Schedule" if schedule_id else "New Schedule")
        self.geometry("600x780")
        self.resizable(False, False)
        self.grab_set()

        self._build_form()
        if schedule_id:
            self._load_schedule()

    def _build_form(self):
        pad = {"padx": 15, "anchor": "w"}

        # Name
        ctk.CTkLabel(self, text="Schedule Name", font=ctk.CTkFont(weight="bold")).pack(pady=(15, 2), **pad)
        self.entry_name = ctk.CTkEntry(self, width=400)
        self.entry_name.pack(**pad)

        # Working directory
        ctk.CTkLabel(self, text="Working Directory", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)
        dir_frame = ctk.CTkFrame(self, fg_color="transparent")
        dir_frame.pack(fill="x", padx=15)
        self.entry_dir = ctk.CTkEntry(dir_frame, width=340)
        self.entry_dir.pack(side="left")
        self.entry_dir.insert(0, ".")
        ctk.CTkButton(dir_frame, text="Browse", width=60,
                      command=self._browse_dir).pack(side="left", padx=5)

        # CLI Provider
        ctk.CTkLabel(self, text="CLI Provider", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)
        provider_frame = ctk.CTkFrame(self, fg_color="transparent")
        provider_frame.pack(fill="x", padx=15)

        self.var_cli_provider = ctk.StringVar(value="Claude Code")
        self.combo_cli_provider = ctk.CTkComboBox(
            provider_frame, values=CLI_PROVIDER_LABELS,
            variable=self.var_cli_provider, width=150,
            command=self._on_cli_provider_change)
        self.combo_cli_provider.pack(side="left")

        self.lbl_provider_desc = ctk.CTkLabel(
            provider_frame, text=CLI_PROVIDER_DESCRIPTIONS["claude"],
            font=ctk.CTkFont(size=11), text_color="#95a5a6", wraplength=380)
        self.lbl_provider_desc.pack(side="left", padx=10)

        # Frequency
        ctk.CTkLabel(self, text="Frequency", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)
        freq_frame = ctk.CTkFrame(self, fg_color="transparent")
        freq_frame.pack(fill="x", padx=15)

        self.var_freq = ctk.StringVar(value="daily")
        self.combo_freq = ctk.CTkComboBox(
            freq_frame, values=["once", "hourly", "daily", "weekly"],
            variable=self.var_freq, width=120, command=self._on_freq_change)
        self.combo_freq.pack(side="left")

        self.combo_dow = ctk.CTkComboBox(freq_frame, values=DAYS_OF_WEEK, width=120)
        self.combo_dow.set("Monday")
        # Hidden by default
        self._dow_visible = False

        # Time
        ctk.CTkLabel(self, text="Scheduled Time (HH:MM)", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)
        self.entry_time = ctk.CTkEntry(self, width=100)
        self.entry_time.insert(0, "09:00")
        self.entry_time.pack(**pad)

        # Terminal Mode
        ctk.CTkLabel(self, text="Terminal Mode", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)
        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=15)

        self.var_terminal_mode = ctk.StringVar(value="Headless")
        self.combo_terminal_mode = ctk.CTkComboBox(
            mode_frame, values=TERMINAL_MODE_LABELS,
            variable=self.var_terminal_mode, width=150,
            command=self._on_terminal_mode_change)
        self.combo_terminal_mode.pack(side="left")

        self.lbl_terminal_desc = ctk.CTkLabel(
            mode_frame, text=TERMINAL_MODE_DESCRIPTIONS["headless"],
            font=ctk.CTkFont(size=11), text_color="#95a5a6", wraplength=400)
        self.lbl_terminal_desc.pack(side="left", padx=10)

        # Prompts
        ctk.CTkLabel(self, text="Prompts (executed in order — type text or load a .md file)",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(10, 2), **pad)

        prompt_toolbar = ctk.CTkFrame(self, fg_color="transparent")
        prompt_toolbar.pack(fill="x", padx=15)
        ctk.CTkButton(prompt_toolbar, text="+ Add Prompt", width=110,
                      command=self._add_prompt).pack(side="left")
        ctk.CTkButton(prompt_toolbar, text="+ Add .md File", width=110, fg_color="#2980b9",
                      hover_color="#1f6aa5",
                      command=self._add_md_file).pack(side="left", padx=5)
        ctk.CTkButton(prompt_toolbar, text="Remove Last", width=100, fg_color="#e74c3c",
                      hover_color="#c0392b",
                      command=self._remove_last_prompt).pack(side="left", padx=5)

        self.prompt_frame = ctk.CTkScrollableFrame(self, height=200)
        self.prompt_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self._prompt_entries: list[ctk.CTkTextbox] = []

        # Save / Cancel
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=10)
        ctk.CTkButton(btn_frame, text="Save", width=100,
                      command=self._save).pack(side="left")
        ctk.CTkButton(btn_frame, text="Cancel", width=100, fg_color="gray",
                      command=self.destroy).pack(side="left", padx=10)

    def _on_freq_change(self, value: str):
        if value == "weekly":
            if not self._dow_visible:
                self.combo_dow.pack(side="left", padx=10)
                self._dow_visible = True
        else:
            if self._dow_visible:
                self.combo_dow.pack_forget()
                self._dow_visible = False

    def _on_cli_provider_change(self, value: str):
        idx = CLI_PROVIDER_LABELS.index(value) if value in CLI_PROVIDER_LABELS else 0
        provider_val = CLI_PROVIDER_VALUES[idx]
        self.lbl_provider_desc.configure(text=CLI_PROVIDER_DESCRIPTIONS[provider_val])

    def _on_terminal_mode_change(self, value: str):
        idx = TERMINAL_MODE_LABELS.index(value) if value in TERMINAL_MODE_LABELS else 0
        mode_val = TERMINAL_MODE_VALUES[idx]
        self.lbl_terminal_desc.configure(text=TERMINAL_MODE_DESCRIPTIONS[mode_val])

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_dir.delete(0, "end")
            self.entry_dir.insert(0, d)

    def _add_prompt(self, text: str = ""):
        idx = len(self._prompt_entries)
        frame = ctk.CTkFrame(self.prompt_frame, fg_color="transparent")
        frame.pack(fill="x", pady=2)

        left = ctk.CTkFrame(frame, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text=f"#{idx + 1}", width=30).pack(side="left")

        # Show file indicator if this is a file reference
        if text.startswith("file:"):
            file_path = text[5:]
            fname = os.path.basename(file_path)
            ctk.CTkLabel(header, text=f"  .md file: {fname}", font=ctk.CTkFont(size=11),
                         text_color="#3498db").pack(side="left", padx=5)

        tb = ctk.CTkTextbox(left, height=60)
        tb.pack(fill="x", padx=(30, 0), pady=(0, 2))
        if text:
            tb.insert("1.0", text)
        self._prompt_entries.append(tb)

        # Browse button on the right side
        btn_col = ctk.CTkFrame(frame, fg_color="transparent", width=70)
        btn_col.pack(side="right", padx=(2, 0))
        btn_col.pack_propagate(False)
        ctk.CTkButton(btn_col, text=".md", width=50, height=26,
                      fg_color="#2980b9", hover_color="#1f6aa5",
                      command=lambda tb_ref=tb: self._browse_md_for_entry(tb_ref)).pack(pady=15)

    def _add_md_file(self):
        """Open file dialog for a .md file and add it as a prompt entry."""
        path = filedialog.askopenfilename(
            title="Select Markdown Prompt File",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
        )
        if path:
            self._add_prompt(f"file:{path}")

    def _browse_md_for_entry(self, textbox: ctk.CTkTextbox):
        """Replace an existing prompt entry with a .md file reference."""
        path = filedialog.askopenfilename(
            title="Select Markdown Prompt File",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
        )
        if path:
            textbox.delete("1.0", "end")
            textbox.insert("1.0", f"file:{path}")

    def _remove_last_prompt(self):
        if self._prompt_entries:
            entry = self._prompt_entries.pop()
            entry.master.master.destroy()

    def _load_schedule(self):
        conn = get_connection(self.db_path)
        sched = get_schedule(conn, self.schedule_id)
        conn.close()
        if not sched:
            return

        self.entry_name.delete(0, "end")
        self.entry_name.insert(0, sched["name"])
        self.entry_dir.delete(0, "end")
        self.entry_dir.insert(0, sched["working_dir"])
        self.var_freq.set(sched["frequency"])
        self._on_freq_change(sched["frequency"])
        if sched["frequency"] == "weekly" and sched.get("day_of_week") is not None:
            self.combo_dow.set(DAYS_OF_WEEK[sched["day_of_week"]])
        self.entry_time.delete(0, "end")
        self.entry_time.insert(0, sched["scheduled_time"])

        # Load terminal mode
        mode_val = sched.get("terminal_mode", "headless")
        if mode_val in TERMINAL_MODE_VALUES:
            idx = TERMINAL_MODE_VALUES.index(mode_val)
            self.var_terminal_mode.set(TERMINAL_MODE_LABELS[idx])
            self._on_terminal_mode_change(TERMINAL_MODE_LABELS[idx])

        # Load CLI provider
        provider_val = sched.get("cli_provider", "claude")
        if provider_val in CLI_PROVIDER_VALUES:
            idx = CLI_PROVIDER_VALUES.index(provider_val)
            self.var_cli_provider.set(CLI_PROVIDER_LABELS[idx])
            self._on_cli_provider_change(CLI_PROVIDER_LABELS[idx])

        for p in sched.get("prompts", []):
            self._add_prompt(p["prompt_text"])

    def _save(self):
        name = self.entry_name.get().strip()
        if not name:
            messagebox.showwarning("Validation", "Schedule name is required.")
            return

        working_dir = self.entry_dir.get().strip() or "."
        frequency = self.var_freq.get()
        scheduled_time = self.entry_time.get().strip()
        day_of_week = DAYS_OF_WEEK.index(self.combo_dow.get()) if frequency == "weekly" else None

        # Validate time format
        try:
            h, m = map(int, scheduled_time.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            messagebox.showwarning("Validation", "Time must be in HH:MM format (e.g., 09:00).")
            return

        prompts = []
        for entry in self._prompt_entries:
            text = entry.get("1.0", "end").strip()
            if text:
                prompts.append(text)

        if not prompts:
            messagebox.showwarning("Validation", "At least one prompt is required.")
            return

        # Extract terminal mode value
        mode_label = self.var_terminal_mode.get()
        idx = TERMINAL_MODE_LABELS.index(mode_label) if mode_label in TERMINAL_MODE_LABELS else 0
        terminal_mode = TERMINAL_MODE_VALUES[idx]

        # Extract CLI provider value
        provider_label = self.var_cli_provider.get()
        idx = CLI_PROVIDER_LABELS.index(provider_label) if provider_label in CLI_PROVIDER_LABELS else 0
        cli_provider = CLI_PROVIDER_VALUES[idx]

        conn = get_connection(self.db_path)
        if self.schedule_id:
            update_schedule(conn, self.schedule_id, name, working_dir, frequency,
                            scheduled_time, day_of_week, prompts, terminal_mode=terminal_mode,
                            cli_provider=cli_provider)
        else:
            create_schedule(conn, name, working_dir, frequency,
                            scheduled_time, day_of_week, prompts, terminal_mode=terminal_mode,
                            cli_provider=cli_provider)
        conn.close()

        if self.on_save:
            self.on_save()
        self.destroy()


# ── Run Detail Dialog ─────────────────────────────────────────────────────

class RunDetailDialog(ctk.CTkToplevel):
    def __init__(self, parent, db_path: str, run_id: int):
        super().__init__(parent)
        self.db_path = db_path

        conn = get_connection(db_path)
        run = get_run(conn, run_id)
        results = get_prompt_results(conn, run_id)
        conn.close()

        if not run:
            self.destroy()
            return

        self.title(f"Run #{run_id} — {run.get('schedule_name', '?')}")
        self.geometry("750x550")

        # Run summary
        summary = ctk.CTkFrame(self)
        summary.pack(fill="x", padx=15, pady=10)

        status = run["status"]
        color = STATUS_COLORS.get(status, "#95a5a6")
        label = STATUS_LABELS.get(status, status)

        ctk.CTkLabel(summary, text=f"Status: {label}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=color).pack(side="left", padx=10)
        ctk.CTkLabel(summary, text=f"Started: {run.get('started_at', '?')}",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=10)

        # Provider
        run_provider = run.get("cli_provider", "claude")
        if run_provider in CLI_PROVIDER_VALUES:
            provider_display = CLI_PROVIDER_LABELS[CLI_PROVIDER_VALUES.index(run_provider)]
        else:
            provider_display = "Claude Code"
        ctk.CTkLabel(summary, text=f"CLI: {provider_display}",
                     font=ctk.CTkFont(size=12), text_color="#7f8c8d").pack(side="left", padx=10)

        # Cost
        if run_provider == "copilot":
            ctk.CTkLabel(summary, text="Cost: N/A",
                         font=ctk.CTkFont(size=12), text_color="#95a5a6").pack(side="left", padx=10)
        else:
            cost = run.get("total_cost_usd", 0) or 0
            if cost > 0:
                ctk.CTkLabel(summary, text=f"Cost: ${cost:.4f}",
                             font=ctk.CTkFont(size=12)).pack(side="left", padx=10)

        # Show terminal mode
        run_mode = run.get("terminal_mode", "headless")
        if run_mode in TERMINAL_MODE_VALUES:
            mode_display = TERMINAL_MODE_LABELS[TERMINAL_MODE_VALUES.index(run_mode)]
        else:
            mode_display = "Headless"
        ctk.CTkLabel(summary, text=f"Mode: {mode_display}",
                     font=ctk.CTkFont(size=12), text_color="#95a5a6").pack(side="left", padx=10)

        # Copilot note
        if run_provider == "copilot":
            ctk.CTkLabel(self, text="(Copilot CLI — text output only, no cost tracking)",
                         font=ctk.CTkFont(size=11, slant="italic"),
                         text_color="#f39c12").pack(padx=15, anchor="w")

        # Interactive mode note
        if run_mode == "interactive":
            ctk.CTkLabel(self, text="(Interactive session — limited output capture)",
                         font=ctk.CTkFont(size=11, slant="italic"),
                         text_color="#f39c12").pack(padx=15, anchor="w")

        # Prompt tabs
        if results:
            tabview = ctk.CTkTabview(self)
            tabview.pack(fill="both", expand=True, padx=15, pady=(0, 15))

            for pr in results:
                tab_name = f"Prompt #{pr['prompt_order'] + 1}"
                tabview.add(tab_name)
                tab = tabview.tab(tab_name)

                pr_status = pr["status"]
                pr_color = STATUS_COLORS.get(pr_status, "#95a5a6")
                pr_label = STATUS_LABELS.get(pr_status, pr_status)

                # Status + duration
                info = ctk.CTkFrame(tab, fg_color="transparent")
                info.pack(fill="x", pady=(5, 5))
                ctk.CTkLabel(info, text=f"Status: {pr_label}",
                             text_color=pr_color, font=ctk.CTkFont(weight="bold")).pack(side="left")
                dur = pr.get("duration_ms", 0) or 0
                if dur > 0:
                    ctk.CTkLabel(info, text=f"  |  {dur / 1000:.1f}s",
                                 text_color="#95a5a6").pack(side="left")
                pr_cost = pr.get("cost_usd", 0) or 0
                if pr_cost > 0:
                    ctk.CTkLabel(info, text=f"  |  ${pr_cost:.4f}",
                                 text_color="#95a5a6").pack(side="left")

                # Prompt text
                ctk.CTkLabel(tab, text="Prompt:", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
                prompt_box = ctk.CTkTextbox(tab, height=50)
                prompt_box.pack(fill="x", pady=(0, 5))
                prompt_box.insert("1.0", pr.get("prompt_text", ""))
                prompt_box.configure(state="disabled")

                # Response / Error
                if pr.get("error_message"):
                    ctk.CTkLabel(tab, text="Error:", font=ctk.CTkFont(weight="bold"),
                                 text_color="#e74c3c").pack(anchor="w")
                    err_box = ctk.CTkTextbox(tab, height=100)
                    err_box.pack(fill="both", expand=True)
                    err_box.insert("1.0", pr["error_message"])
                    err_box.configure(state="disabled")
                elif pr.get("result_text"):
                    ctk.CTkLabel(tab, text="Response:", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
                    res_box = ctk.CTkTextbox(tab)
                    res_box.pack(fill="both", expand=True)
                    res_box.insert("1.0", pr["result_text"])
                    res_box.configure(state="disabled")
