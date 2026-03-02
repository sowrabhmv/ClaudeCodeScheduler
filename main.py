"""Entry point for Claude Code Scheduler — singleton, tray, startup."""

import sys
import os
import argparse
import logging
import ctypes

# Ensure our directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_connection, init_db, get_all_schedules, get_setting, DB_PATH
from scheduler_engine import SchedulerEngine
from tray import TrayManager
from ui import App

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("main")

MUTEX_NAME = "ClaudeCodeScheduler_SingleInstance"


def acquire_singleton() -> bool:
    """Use a Windows named mutex to enforce single instance."""
    if os.name != "nt":
        return True
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            return False
        return True
    except Exception:
        return True


def main():
    parser = argparse.ArgumentParser(description="Claude Code Scheduler")
    parser.add_argument("--background", action="store_true",
                        help="Start hidden in system tray")
    args = parser.parse_args()

    if not acquire_singleton():
        log.warning("Another instance is already running. Exiting.")
        # Try to bring existing window to front
        if os.name == "nt":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, "Claude Code Scheduler")
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        sys.exit(0)

    # Initialize database
    conn = get_connection(DB_PATH)
    init_db(conn)
    conn.close()

    # Auto-register Windows startup if setting is enabled
    _auto_register_startup()

    # Create engine
    engine = SchedulerEngine(db_path=DB_PATH)

    # Create UI (possibly hidden)
    app = App(db_path=DB_PATH, engine=engine, start_hidden=args.background)

    # Create tray
    tray = TrayManager(
        on_show_window=app.show_window,
        on_quit=lambda: _quit_all(app, tray, engine),
        on_pause_toggle=lambda paused: setattr(engine, "paused", paused),
        on_run_all=lambda: _run_all_now(engine),
    )

    # Engine event hook for tray notifications
    original_callback = engine.ui_callback

    def combined_callback(event, **kwargs):
        if original_callback:
            original_callback(event, **kwargs)
        if event == "run_finished":
            status = kwargs.get("status", "")
            schedule_id = kwargs.get("schedule_id")
            conn2 = get_connection(DB_PATH)
            sched_name = ""
            try:
                from database import get_schedule
                s = get_schedule(conn2, schedule_id)
                sched_name = s["name"] if s else ""
            finally:
                conn2.close()

            notify_success = get_setting(get_connection(DB_PATH), "notify_on_success", "true") == "true"
            notify_failure = get_setting(get_connection(DB_PATH), "notify_on_failure", "true") == "true"

            if status == "success" and notify_success:
                tray.notify("Schedule Complete", f"{sched_name}: Success")
            elif status in ("failure", "partial_failure", "error") and notify_failure:
                tray.notify("Schedule Failed", f"{sched_name}: {status.replace('_', ' ').title()}")

        # Update tray info
        conn3 = get_connection(DB_PATH)
        schedules = get_all_schedules(conn3)
        conn3.close()
        enabled_count = sum(1 for s in schedules if s["enabled"])
        next_info = engine.get_next_run_info() or ""
        tray.update_info(schedule_count=enabled_count, next_run=next_info)

    engine.ui_callback = combined_callback

    # Start engine and tray
    engine.start()
    tray.start()

    # Initial tray info
    conn = get_connection(DB_PATH)
    schedules = get_all_schedules(conn)
    conn.close()
    enabled_count = sum(1 for s in schedules if s["enabled"])
    tray.update_info(schedule_count=enabled_count, next_run=engine.get_next_run_info() or "")

    log.info("Claude Code Scheduler started (background=%s)", args.background)

    # Run Tk main loop
    app.mainloop()

    # Cleanup on exit
    engine.stop()
    tray.stop()


def _auto_register_startup():
    """Register app in Windows startup registry if the setting is enabled."""
    if os.name != "nt":
        return
    conn = get_connection(DB_PATH)
    enabled = get_setting(conn, "run_on_startup", "true") == "true"
    conn.close()
    if not enabled:
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "ClaudeCodeScheduler"
        exe = sys.executable
        script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
        pythonw = exe.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = exe
        value = f'"{pythonw}" "{script}" --background'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        log.info("Registered in Windows startup: %s", value)
    except Exception as e:
        log.warning("Failed to register startup: %s", e)


def _quit_all(app: App, tray: TrayManager, engine: SchedulerEngine):
    engine.stop()
    tray.stop()
    try:
        app.quit_app()
    except Exception:
        pass
    os._exit(0)


def _run_all_now(engine: SchedulerEngine):
    conn = get_connection(DB_PATH)
    schedules = get_all_schedules(conn)
    conn.close()
    for sched in schedules:
        if sched["enabled"]:
            engine.execute_now(sched["id"])


if __name__ == "__main__":
    main()
