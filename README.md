# Claude Code Scheduler

A Windows desktop app for scheduling prompts that run on [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code). Create schedules, chain multiple prompts per session, use `.md` files as prompts, persist across reboots, and track full run history with success/fail indicators.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- **Schedule prompts** to run on Claude Code CLI at set times (once, hourly, daily, weekly)
- **Chain multiple prompts** per schedule — uses `--resume` to continue the same session
- **Use `.md` files as prompts** — reference Markdown files that are read at runtime (edit the file, next run picks up changes)
- **Terminal mode** — choose per-schedule how Claude runs: **Headless** (hidden), **Visible** (watch in console), or **Interactive** (full TUI)
- **Runs with `--dangerously-skip-permissions`** by default for full autonomous execution (configurable)
- **System tray** — minimizes to tray, shows status, notifications on completion/failure
- **Runs on Windows startup** — auto-registers in `HKCU\...\Run` to launch in background on boot
- **Full run history** — every run tracked with per-prompt status, response text, cost, duration
- **Single instance** — mutex prevents duplicate processes
- **Dark mode UI** — built with customtkinter

## Screenshots

| Schedules | History | Settings |
|-----------|---------|----------|
| Card list with enable/disable toggles and colored status badges | Filterable run list with details drill-down | Claude CLI path, startup, tray, notifications |

---

## Installation

### Option A: Download the Installer (Recommended)

1. Go to [Releases](../../releases)
2. Download `ClaudeCodeScheduler_Setup_X.X.X.exe`
3. Run the installer — it will add a Start Menu shortcut and optionally a desktop icon
4. Launch "Claude Code Scheduler" from Start Menu

**Prerequisite:** [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) must be installed and available in PATH.

### Option B: Run from Source

```bash
# Clone the repository
git clone https://github.com/sowrabhm/ClaudeCodeScheduler.git
cd ClaudeCodeScheduler

# Install dependencies
pip install -r requirements.txt

# Run the app
python main.py
```

Or use the install helper:

```bash
install.bat
```

### Option C: Build Your Own Installer

```bash
# Install build dependencies
pip install -r requirements-dev.txt

# Build the standalone .exe
build.bat

# (Optional) Create Windows installer — requires Inno Setup 6
#   Download from https://jrsoftware.org/isinfo.php
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

---

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| **Claude Code CLI** | The `claude` command must be available in PATH (or configured in Settings) |
| **Python 3.9+** | Only needed for Option B/C (not needed if using the installer) |
| **Windows 10/11** | System tray, startup registry, and mutex require Windows |

### Installing Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

See [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) for details.

---

## Usage

### Creating a Schedule

1. Click **"+ New Schedule"**
2. Fill in:
   - **Name** — a label for this schedule
   - **Working Directory** — the folder Claude CLI runs in (use Browse)
   - **Frequency** — once, hourly, daily, or weekly
   - **Time** — HH:MM format
   - **Terminal Mode** — how Claude runs (see [Terminal Modes](#terminal-modes) below)
3. Add prompts:
   - **"+ Add Prompt"** — type prompt text directly
   - **"+ Add .md File"** — select a Markdown file (read at runtime, so edits are picked up)
   - Each prompt entry also has a **".md"** button to swap it to a file reference
4. Click **Save**

### Terminal Modes

Each schedule can be configured with one of three terminal modes:

| Mode | Window | User Interaction | Output Capture | Use Case |
|------|--------|------------------|----------------|----------|
| **Headless** (default) | None — runs hidden | None | Full JSON response | Fully automated, unattended runs |
| **Visible** | Console window opens | Read-only (watch) | Full JSON via temp file | Monitor progress in real-time |
| **Interactive** | Console window opens | Full Claude TUI | Session ID + exit code | Hands-on sessions where you guide Claude |

**Headless** runs completely in the background with `CREATE_NO_WINDOW`. This is the default and matches the original behavior.

**Visible** opens a console window titled "Claude Code - \<schedule name\>" so you can watch Claude work. Output is still captured as JSON and appears in History with full response details.

**Interactive** launches the full Claude TUI in a new console window. Only the first prompt is used (as an initial message). You interact with Claude directly — ask follow-up questions, approve actions, etc. Remaining prompts in the schedule are skipped. There is no timeout; the run completes when you exit Claude. History shows the session ID and exit code but not full response text.

### Prompt Chaining

When a schedule has multiple prompts (in **Headless** or **Visible** mode), they execute in order within the same Claude session:

- **First prompt:** `claude -p "prompt" --dangerously-skip-permissions --output-format json`
- **Subsequent prompts:** `claude -p "prompt" --resume <session_id> --dangerously-skip-permissions --output-format json`

If any prompt fails, remaining prompts are **skipped** and the run is marked as **Partial Failure**.

> **Note:** In **Interactive** mode, only the first prompt is sent as an initial message. The user controls the session from there, so prompt chaining does not apply.

### Using `.md` Files as Prompts

Instead of typing prompts inline, you can reference `.md` files:

1. Click **"+ Add .md File"** and select a Markdown file
2. The file path is stored as `file:C:\path\to\prompt.md`
3. At execution time, the file is read and its contents are sent as the prompt
4. **Benefit:** Edit the `.md` file anytime — the next scheduled run picks up changes

### Viewing History

- Switch to the **History** tab
- Filter by schedule name
- Click **Details** on any run to see per-prompt results (status, response text, errors, cost, duration)

### Status Indicators

| Status | Color | Meaning |
|--------|-------|---------|
| Success | Green | All prompts completed successfully |
| Failed | Red | First prompt failed |
| Partial | Orange | Some prompts succeeded, then one failed |
| Running | Blue | Currently executing |

---

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Claude CLI Executable | `claude` | Path to the Claude CLI binary |
| `--dangerously-skip-permissions` | On | Allows Claude to execute without permission prompts |
| Check Interval | 30s | How often the scheduler checks for due schedules |
| History Retention | 90 days | Auto-purge old run history |
| Run on Windows Startup | On | Auto-start in background on boot |
| Minimize to System Tray | On | Window X button hides to tray instead of quitting |
| Notify on Success | On | Tray notification when a schedule completes |
| Notify on Failure | On | Tray notification when a schedule fails |

---

## System Tray

When minimized, the app runs in the system tray with an orange "C" icon:

- **Double-click** — Show window
- **Right-click** menu:
  - Show Window
  - Schedule count / Next run time
  - Pause All / Resume All
  - Run All Now
  - Quit

---

## Architecture

```
ClaudeCodeScheduler/
├── main.py              # Entry point, singleton mutex, startup registration
├── ui.py                # customtkinter UI: window, sidebar, tabs, dialogs
├── scheduler_engine.py  # Background timer thread, due-check logic
├── claude_runner.py     # Claude CLI subprocess + JSON parsing + .md resolve
├── database.py          # SQLite schema, CRUD for schedules/runs/settings
├── tray.py              # pystray system tray icon + notifications
├── version.py           # Version info
├── gen_icon.py          # Generates app.ico from code
├── app.ico              # Multi-resolution Windows icon
├── build.bat            # PyInstaller build script
├── installer.iss        # Inno Setup installer script
├── install.bat          # Install-from-source helper
├── requirements.txt     # Runtime dependencies
├── requirements-dev.txt # Build dependencies
├── LICENSE              # MIT License
└── README.md            # This file
```

### Database (SQLite)

Stored as `scheduler.db` in the app directory:

| Table | Purpose |
|-------|---------|
| `schedules` | Name, directory, frequency, time, terminal mode, enabled |
| `schedule_prompts` | Ordered prompt list per schedule |
| `runs` | Execution history with status, cost, and terminal mode |
| `prompt_results` | Per-prompt status, response, errors |
| `settings` | Key-value app configuration |

---

## Command-Line Arguments

```
python main.py [--background]
```

| Argument | Description |
|----------|-------------|
| `--background` | Start hidden in the system tray (used for startup) |

---

## Building from Source

### Standalone Executable (PyInstaller)

```bash
pip install pyinstaller
build.bat
```

Output: `dist\ClaudeCodeScheduler\ClaudeCodeScheduler.exe`

### Windows Installer (Inno Setup)

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php)
2. First run `build.bat` to create the exe
3. Then:

```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `Output\ClaudeCodeScheduler_Setup_1.0.0.exe`

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m "Add my feature"`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

[MIT License](LICENSE) — free for personal and commercial use.

---

## Acknowledgments

- Built for use with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) by Anthropic
- UI powered by [customtkinter](https://github.com/TomSchimansky/CustomTkinter)
- System tray via [pystray](https://github.com/moses-palmer/pystray)
