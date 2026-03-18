# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code Scheduler is a Windows desktop app for scheduling prompts that run on Claude Code CLI. Users create schedules (once/hourly/daily/weekly), chain multiple prompts per session using `--resume`, reference `.md` files as prompts, and track run history with cost/status tracking. Built with Python 3.9+, customtkinter (dark mode UI), pystray (system tray), and SQLite.

## Commands

### Run from source
```bash
pip install -r requirements.txt
python main.py                    # Normal launch
pythonw main.py --background      # Start hidden in system tray
```

### Build standalone exe (PyInstaller)
```bash
pip install -r requirements-dev.txt
build.bat
# Output: dist\ClaudeCodeScheduler\ClaudeCodeScheduler.exe
```

### Build Windows installer (requires Inno Setup 6)
```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

### Generate app icon
```bash
python gen_icon.py
```

## Architecture

Six Python modules with clear responsibilities and a single entry point:

```
main.py → App (ui.py) ←→ SchedulerEngine (scheduler_engine.py) → claude_runner.py
                ↕                        ↕
            database.py              database.py
                                         ↕
                                    TrayManager (tray.py)
```

**`main.py`** — Entry point. Acquires a Windows named mutex for single-instance enforcement, initializes the DB, wires together the engine/UI/tray, registers Windows startup via `HKCU\...\Run`, and runs the Tk main loop.

**`ui.py`** — The full customtkinter UI. `App` (CTk subclass) owns a sidebar with three tabs: Schedules (card list with enable/disable/run-now), History (filterable run list with detail drill-down), and Settings. `ScheduleDialog` and `RunDetailDialog` are CTkToplevel modals. Engine events arrive on background threads and are dispatched to the Tk main thread via `self.after(0, ...)`.

**`scheduler_engine.py`** — `SchedulerEngine` runs a daemon thread that polls every N seconds (configurable). `_check_due()` evaluates each enabled schedule against a tolerance window (`interval + 5s`). Due schedules are dispatched to individual daemon threads. Three execution paths: `_execute_headless_or_visible` (prompt chain with JSON capture), `_execute_interactive` (Claude TUI, first prompt only). A lock-protected `_running_schedules` set prevents concurrent runs of the same schedule.

**`claude_runner.py`** — Subprocess wrapper for the Claude CLI. Three modes:
- `run_prompt()` — headless, `CREATE_NO_WINDOW`, captures stdout JSON
- `run_prompt_visible()` — launches PowerShell in `CREATE_NEW_CONSOLE`, writes prompt to temp file, redirects JSON output to temp file, parses after completion
- `run_interactive()` — launches Claude TUI via PowerShell in new console with `--session-id`, waits indefinitely
All modes use `resolve_prompt_text()` to handle `file:` prefixed prompt references. `_parse_json_response()` handles Claude CLI JSON output format (extracts `session_id`, `cost_usd`, `result` text blocks, `is_error`).

**`database.py`** — SQLite with WAL mode and foreign keys. Five tables: `schedules`, `schedule_prompts` (ordered prompt list), `runs`, `prompt_results`, `settings` (key-value). Migrations add `terminal_mode` columns via `_migrate_add_terminal_mode` / `_migrate_add_run_terminal_mode`. All functions take an explicit `conn` parameter.

**`tray.py`** — `TrayManager` wraps pystray with a programmatic orange "C" icon. Runs in a daemon thread. Context menu includes show/pause/run-all/quit. Supports Windows toast notifications.

## Key Patterns

- **Thread safety**: Engine runs schedules on daemon threads; UI updates funnel through `self.after(0, callback)`. `_running_schedules` set is protected by a `threading.Lock`.
- **Prompt chaining**: First prompt gets a fresh session; subsequent prompts use `--resume <session_id>`. On failure, remaining prompts are marked `skipped`.
- **File prompts**: Stored as `file:C:\path\to\file.md` in the database. Resolved at execution time via `resolve_prompt_text()`, so edits to the .md are picked up on next run.
- **Visible/Interactive modes**: Use PowerShell scripts written to temp files to avoid shell escaping issues. Temp dirs are cleaned up in `finally` blocks.
- **DB connections**: Short-lived — opened, used, closed. `get_connection()` returns a `Row`-factory connection.

## Database

SQLite file at `scheduler.db` in the app directory. Schema lives in `database.py:init_db()`. Settings are seeded with defaults on first run. Run `purge_old_runs(conn, days)` for retention cleanup.

## Platform

Windows-only. Uses `ctypes.windll` (mutex, FindWindow), `winreg` (startup), `CREATE_NO_WINDOW`/`CREATE_NEW_CONSOLE` subprocess flags, and PowerShell for visible/interactive terminal modes.
