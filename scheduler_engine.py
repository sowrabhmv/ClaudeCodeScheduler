"""Background scheduler engine — daemon thread that checks for due schedules."""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable

from database import (
    get_connection, get_all_schedules, get_schedule, get_setting,
    create_run, finish_run, create_prompt_result, update_prompt_result,
)
from claude_runner import run_prompt, run_prompt_visible, run_interactive, PromptResponse

log = logging.getLogger("scheduler_engine")


class SchedulerEngine:
    def __init__(self, db_path: str, ui_callback: Optional[Callable] = None):
        self.db_path = db_path
        self.ui_callback = ui_callback  # called on tk main thread via root.after
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = False
        self._running_schedules: set[int] = set()
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Scheduler engine started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Scheduler engine stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool):
        self._paused = value

    # ── Main loop ─────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                if not self._paused:
                    self._check_due()
            except Exception:
                log.exception("Error in scheduler check loop")
            # Sleep in small increments so stop is responsive
            conn = get_connection(self.db_path)
            interval = int(get_setting(conn, "check_interval_seconds", "30"))
            conn.close()
            for _ in range(interval * 2):  # check every 0.5s
                if self._stop_event.is_set():
                    return
                time.sleep(0.5)

    def _check_due(self):
        conn = get_connection(self.db_path)
        try:
            interval = int(get_setting(conn, "check_interval_seconds", "30"))
            schedules = get_all_schedules(conn)
        finally:
            conn.close()

        now = datetime.now()
        for sched in schedules:
            if not sched["enabled"]:
                continue
            with self._lock:
                if sched["id"] in self._running_schedules:
                    continue
            if self._is_due(sched, now, interval):
                self._spawn_execution(sched)

    def _is_due(self, sched: dict, now: datetime, interval: int) -> bool:
        """Check if a schedule is due to run within the tolerance window."""
        freq = sched["frequency"]
        time_str = sched["scheduled_time"]  # "HH:MM"
        try:
            sched_hour, sched_min = map(int, time_str.split(":"))
        except ValueError:
            return False

        tolerance = timedelta(seconds=interval + 5)

        if freq == "once":
            # Check if never run and time matches
            conn = get_connection(self.db_path)
            runs = conn.execute(
                "SELECT COUNT(*) as cnt FROM runs WHERE schedule_id=?", (sched["id"],)
            ).fetchone()
            conn.close()
            if runs["cnt"] > 0:
                return False
            target = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
            return abs(now - target) <= tolerance

        elif freq == "hourly":
            target = now.replace(minute=sched_min, second=0, microsecond=0)
            return abs(now - target) <= tolerance

        elif freq == "daily":
            target = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
            return abs(now - target) <= tolerance

        elif freq == "weekly":
            dow = sched.get("day_of_week")
            if dow is None:
                return False
            if now.weekday() != dow:
                return False
            target = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
            return abs(now - target) <= tolerance

        return False

    # ── Execution ─────────────────────────────────────────────────────

    def _spawn_execution(self, sched: dict):
        with self._lock:
            self._running_schedules.add(sched["id"])
        t = threading.Thread(
            target=self._execute_schedule, args=(sched,), daemon=True
        )
        t.start()

    def execute_now(self, schedule_id: int):
        """Trigger immediate execution (called from UI 'Run Now' button)."""
        conn = get_connection(self.db_path)
        sched = get_schedule(conn, schedule_id)
        conn.close()
        if not sched:
            return
        with self._lock:
            if schedule_id in self._running_schedules:
                return  # already running
        self._spawn_execution(sched)

    def _execute_schedule(self, sched: dict):
        schedule_id = sched["id"]
        terminal_mode = sched.get("terminal_mode", "headless")
        conn = get_connection(self.db_path)
        try:
            claude_exe = get_setting(conn, "claude_executable", "claude")
            skip_perms = get_setting(conn, "dangerously_skip_permissions", "true") == "true"
            run_id = create_run(conn, schedule_id, terminal_mode=terminal_mode)
            self._notify("run_started", schedule_id=schedule_id, run_id=run_id)

            prompts = sched.get("prompts", [])
            if not prompts:
                finish_run(conn, run_id, "error")
                self._notify("run_finished", schedule_id=schedule_id, run_id=run_id, status="error")
                return

            if terminal_mode == "interactive":
                self._execute_interactive(conn, sched, run_id, prompts, claude_exe)
            else:
                self._execute_headless_or_visible(
                    conn, sched, run_id, prompts, claude_exe, skip_perms, terminal_mode
                )

        except Exception as e:
            log.exception("Error executing schedule %s", schedule_id)
            try:
                finish_run(conn, run_id, "error")
            except Exception:
                pass
            self._notify("run_finished", schedule_id=schedule_id, run_id=run_id, status="error")
        finally:
            conn.close()
            with self._lock:
                self._running_schedules.discard(schedule_id)

    def _execute_headless_or_visible(
        self, conn, sched, run_id, prompts, claude_exe, skip_perms, terminal_mode
    ):
        """Execute prompts in headless or visible mode (prompt chain with JSON capture)."""
        schedule_id = sched["id"]
        runner = run_prompt_visible if terminal_mode == "visible" else run_prompt

        session_id = None
        total_cost = 0.0
        all_success = True
        any_success = False

        for i, prompt in enumerate(prompts):
            pr_id = create_prompt_result(
                conn, run_id, prompt["id"], prompt["prompt_order"], prompt["prompt_text"]
            )
            update_prompt_result(conn, pr_id, "running")
            self._notify("prompt_started", schedule_id=schedule_id, run_id=run_id, prompt_order=i)

            kwargs = dict(
                prompt_text=prompt["prompt_text"],
                working_dir=sched["working_dir"],
                session_id=session_id,
                claude_executable=claude_exe,
                skip_permissions=skip_perms,
            )
            if terminal_mode == "visible":
                kwargs["schedule_name"] = sched["name"]

            resp = runner(**kwargs)

            if resp.success:
                update_prompt_result(
                    conn, pr_id, "success",
                    result_text=resp.result_text,
                    result_json=resp.result_json,
                    cost_usd=resp.cost_usd,
                    duration_ms=resp.duration_ms,
                )
                if resp.session_id:
                    session_id = resp.session_id
                total_cost += resp.cost_usd
                any_success = True
            else:
                update_prompt_result(
                    conn, pr_id, "failure",
                    error_message=resp.error_message,
                    cost_usd=resp.cost_usd,
                    duration_ms=resp.duration_ms,
                )
                total_cost += resp.cost_usd
                all_success = False
                # Skip remaining prompts
                for j in range(i + 1, len(prompts)):
                    skip_id = create_prompt_result(
                        conn, run_id, prompts[j]["id"],
                        prompts[j]["prompt_order"], prompts[j]["prompt_text"],
                    )
                    update_prompt_result(conn, skip_id, "skipped",
                                         error_message="Skipped due to previous failure")
                break

            self._notify("prompt_finished", schedule_id=schedule_id, run_id=run_id, prompt_order=i)

        # Determine overall status
        if all_success:
            status = "success"
        elif any_success:
            status = "partial_failure"
        else:
            status = "failure"

        finish_run(conn, run_id, status, session_id=session_id, total_cost_usd=total_cost)
        self._notify("run_finished", schedule_id=schedule_id, run_id=run_id, status=status)

    def _execute_interactive(self, conn, sched, run_id, prompts, claude_exe):
        """Execute in interactive mode — launch Claude TUI with the first prompt."""
        schedule_id = sched["id"]
        first_prompt = prompts[0]

        # Create prompt result for the first prompt
        pr_id = create_prompt_result(
            conn, run_id, first_prompt["id"], first_prompt["prompt_order"],
            first_prompt["prompt_text"]
        )
        update_prompt_result(conn, pr_id, "running")
        self._notify("prompt_started", schedule_id=schedule_id, run_id=run_id, prompt_order=0)

        resp = run_interactive(
            working_dir=sched["working_dir"],
            claude_executable=claude_exe,
            initial_prompt=first_prompt["prompt_text"],
            schedule_name=sched["name"],
        )

        if resp.success:
            update_prompt_result(
                conn, pr_id, "success",
                result_text=resp.result_text,
                duration_ms=resp.duration_ms,
            )
        else:
            update_prompt_result(
                conn, pr_id, "failure",
                error_message=resp.error_message,
                duration_ms=resp.duration_ms,
            )

        # Mark remaining prompts as skipped (interactive mode uses only the first)
        for j in range(1, len(prompts)):
            skip_id = create_prompt_result(
                conn, run_id, prompts[j]["id"],
                prompts[j]["prompt_order"], prompts[j]["prompt_text"],
            )
            update_prompt_result(conn, skip_id, "skipped",
                                 error_message="Skipped — interactive mode uses first prompt only")

        status = "success" if resp.success else "failure"
        finish_run(conn, run_id, status, session_id=resp.session_id)
        self._notify("run_finished", schedule_id=schedule_id, run_id=run_id, status=status)

    def _notify(self, event: str, **kwargs):
        if self.ui_callback:
            try:
                self.ui_callback(event, **kwargs)
            except Exception:
                log.exception("UI callback error for event %s", event)

    # ── Queries ───────────────────────────────────────────────────────

    def get_next_run_info(self) -> Optional[str]:
        """Return human-readable string for the next scheduled run."""
        conn = get_connection(self.db_path)
        try:
            schedules = get_all_schedules(conn)
        finally:
            conn.close()

        now = datetime.now()
        nearest = None
        nearest_name = ""

        for sched in schedules:
            if not sched["enabled"]:
                continue
            try:
                h, m = map(int, sched["scheduled_time"].split(":"))
            except ValueError:
                continue

            freq = sched["frequency"]
            if freq == "once":
                # Check if already run
                conn2 = get_connection(self.db_path)
                runs = conn2.execute(
                    "SELECT COUNT(*) as cnt FROM runs WHERE schedule_id=?", (sched["id"],)
                ).fetchone()
                conn2.close()
                if runs["cnt"] > 0:
                    continue
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target < now:
                    continue
            elif freq == "hourly":
                target = now.replace(minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(hours=1)
            elif freq == "daily":
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
            elif freq == "weekly":
                dow = sched.get("day_of_week", 0)
                days_ahead = dow - now.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                target = now + timedelta(days=days_ahead)
                target = target.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(weeks=1)
            else:
                continue

            if nearest is None or target < nearest:
                nearest = target
                nearest_name = sched["name"]

        if nearest:
            delta = nearest - now
            if delta.total_seconds() < 60:
                return f"{nearest_name}: <1 min"
            elif delta.total_seconds() < 3600:
                mins = int(delta.total_seconds() // 60)
                return f"{nearest_name}: {mins}m"
            else:
                return f"{nearest_name}: {nearest.strftime('%H:%M')}"
        return None

    def is_schedule_running(self, schedule_id: int) -> bool:
        with self._lock:
            return schedule_id in self._running_schedules
