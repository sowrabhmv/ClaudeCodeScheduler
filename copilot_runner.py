"""GitHub Copilot CLI subprocess wrapper with text output parsing and session chaining."""

import subprocess
import time
import os
import tempfile
from typing import Optional

from claude_runner import PromptResponse, resolve_prompt_text, _find_powershell

CREATE_NEW_CONSOLE = 0x00000010  # Windows flag for new console window


def _parse_text_response(
    response: PromptResponse,
    raw: str,
    returncode: int,
    stderr: str,
    elapsed_ms: int,
) -> PromptResponse:
    """Parse plain text output from Copilot CLI into a PromptResponse."""
    response.duration_ms = elapsed_ms
    response.result_text = raw
    response.cost_usd = 0.0
    response.session_id = ""

    if returncode == 0:
        response.success = True
        response.is_error = False
    else:
        response.success = False
        response.is_error = True
        response.error_message = stderr.strip() or raw or f"Exit code {returncode}"

    return response


def run_prompt(
    prompt_text: str,
    working_dir: str = ".",
    is_continuation: bool = False,
    copilot_executable: str = "copilot",
    timeout_seconds: int = 600,
    skip_permissions: bool = True,
    use_autopilot: bool = True,
    max_continues: int = 50,
) -> PromptResponse:
    """
    Run a single prompt via the Copilot CLI (headless mode).

    First prompt:  copilot -p "prompt" --autopilot --yolo --silent --max-autopilot-continues 50
    Subsequent:    copilot -p "prompt" --continue --autopilot --yolo --silent --max-autopilot-continues 50

    If prompt_text starts with 'file:', the referenced .md file is read at runtime.
    """
    response = PromptResponse()
    try:
        actual_prompt = resolve_prompt_text(prompt_text)
    except FileNotFoundError as e:
        response.success = False
        response.is_error = True
        response.error_message = str(e)
        return response

    cmd = [copilot_executable, "-p", actual_prompt]
    if use_autopilot:
        cmd.append("--autopilot")
    if skip_permissions:
        cmd.append("--yolo")
    cmd.append("--silent")
    if max_continues > 0:
        cmd.extend(["--max-autopilot-continues", str(max_continues)])
    if is_continuation:
        cmd.append("--continue")

    start_ms = time.monotonic_ns() // 1_000_000

    try:
        cwd = working_dir if os.path.isdir(working_dir) else "."
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        response.duration_ms = elapsed_ms

        raw = proc.stdout.strip()
        return _parse_text_response(response, raw, proc.returncode, proc.stderr, elapsed_ms)

    except subprocess.TimeoutExpired:
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        response.duration_ms = elapsed_ms
        response.success = False
        response.is_error = True
        response.error_message = f"Timed out after {timeout_seconds}s"

    except FileNotFoundError:
        response.success = False
        response.is_error = True
        response.error_message = (
            f"Copilot executable not found: '{copilot_executable}'. "
            "Make sure GitHub Copilot CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    return response


def run_prompt_visible(
    prompt_text: str,
    working_dir: str = ".",
    is_continuation: bool = False,
    copilot_executable: str = "copilot",
    timeout_seconds: int = 600,
    skip_permissions: bool = True,
    use_autopilot: bool = True,
    max_continues: int = 50,
    schedule_name: str = "",
) -> PromptResponse:
    """
    Run a prompt in a visible console window. Output is captured via a temp file.

    Uses PowerShell to launch a new console window with the schedule name as title.
    The prompt is written to a temp file to avoid all shell escaping issues.
    Output is redirected to a temp file and parsed after completion.
    """
    response = PromptResponse()
    try:
        actual_prompt = resolve_prompt_text(prompt_text)
    except FileNotFoundError as e:
        response.success = False
        response.is_error = True
        response.error_message = str(e)
        return response

    start_ms = time.monotonic_ns() // 1_000_000

    # Create temp files: prompt input, text output, and PowerShell script
    tmp_dir = tempfile.mkdtemp(prefix="copilot_visible_")
    prompt_file = os.path.join(tmp_dir, "prompt.txt")
    output_file = os.path.join(tmp_dir, "output.txt")
    script_file = os.path.join(tmp_dir, "run.ps1")

    try:
        cwd = working_dir if os.path.isdir(working_dir) else "."
        window_title = f"Copilot - {schedule_name}" if schedule_name else "GitHub Copilot"

        # Write prompt to file (avoids all escaping issues)
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(actual_prompt)

        if os.name == "nt":
            # Build PowerShell script
            ps_lines = [
                f"$Host.UI.RawUI.WindowTitle = '{window_title.replace(chr(39), chr(39)+chr(39))}'",
                f"$prompt = Get-Content -Path '{prompt_file}' -Raw -Encoding UTF8",
                f"$args_list = @('-p', $prompt)",
            ]
            if use_autopilot:
                ps_lines.append("$args_list += '--autopilot'")
            if skip_permissions:
                ps_lines.append("$args_list += '--yolo'")
            # No --silent in visible mode — user watches output
            if max_continues > 0:
                ps_lines.append("$args_list += '--max-autopilot-continues'")
                ps_lines.append(f"$args_list += '{max_continues}'")
            if is_continuation:
                ps_lines.append("$args_list += '--continue'")
            # Status banner so the console window is not blank
            ps_lines.append("Write-Host '--- Claude Code Scheduler ---' -ForegroundColor Cyan")
            ps_lines.append("Write-Host ''")
            # Run Copilot — stdout captured to file, stderr (progress) shows in console
            ps_lines.append(
                f"& '{copilot_executable}' @args_list "
                f"| Out-File -FilePath '{output_file}' -Encoding UTF8"
            )
            ps_lines.append("Write-Host '' ; Write-Host 'Done.' -ForegroundColor Cyan")
            ps_lines.append(f"exit $LASTEXITCODE")

            with open(script_file, "w", encoding="utf-8") as f:
                f.write("\n".join(ps_lines))

            ps_exe = _find_powershell()
            proc = subprocess.Popen(
                [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_file],
                cwd=cwd,
                creationflags=CREATE_NEW_CONSOLE,
            )
        else:
            # On non-Windows, run directly with output redirect
            cmd_parts = [copilot_executable, "-p", actual_prompt]
            if use_autopilot:
                cmd_parts.append("--autopilot")
            if skip_permissions:
                cmd_parts.append("--yolo")
            if max_continues > 0:
                cmd_parts.extend(["--max-autopilot-continues", str(max_continues)])
            if is_continuation:
                cmd_parts.append("--continue")
            proc = subprocess.Popen(
                cmd_parts,
                stdout=open(output_file, "w"),
                stderr=subprocess.STDOUT,
                cwd=cwd,
            )

        proc.wait(timeout=timeout_seconds)
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        response.duration_ms = elapsed_ms

        # Read output from temp file
        try:
            with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
                # PowerShell Out-File may add a UTF-8 BOM — strip it
                if raw.startswith("\ufeff"):
                    raw = raw[1:]
        except Exception:
            raw = ""

        return _parse_text_response(response, raw, proc.returncode, "", elapsed_ms)

    except subprocess.TimeoutExpired:
        proc.kill()
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        response.duration_ms = elapsed_ms
        response.success = False
        response.is_error = True
        response.error_message = f"Timed out after {timeout_seconds}s"

    except FileNotFoundError:
        response.success = False
        response.is_error = True
        response.error_message = (
            f"Copilot executable not found: '{copilot_executable}'. "
            "Make sure GitHub Copilot CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    finally:
        # Clean up temp directory
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    return response


def run_interactive(
    working_dir: str = ".",
    copilot_executable: str = "copilot",
    initial_prompt: Optional[str] = None,
    schedule_name: str = "",
    skip_permissions: bool = True,
) -> PromptResponse:
    """
    Launch Copilot in interactive mode in a new console window.

    Uses PowerShell to open a new console with the schedule name as title.
    The user gets full Copilot interaction. No --autopilot flag.
    No timeout — user controls the session.
    """
    response = PromptResponse()

    # Resolve initial prompt if provided
    actual_prompt = None
    if initial_prompt:
        try:
            actual_prompt = resolve_prompt_text(initial_prompt)
        except FileNotFoundError as e:
            response.success = False
            response.is_error = True
            response.error_message = str(e)
            return response

    start_ms = time.monotonic_ns() // 1_000_000

    # Create temp directory for script and prompt file
    tmp_dir = tempfile.mkdtemp(prefix="copilot_interactive_")
    script_file = os.path.join(tmp_dir, "run.ps1")

    try:
        cwd = working_dir if os.path.isdir(working_dir) else "."
        window_title = f"Copilot - {schedule_name}" if schedule_name else "GitHub Copilot (Interactive)"

        if os.name == "nt":
            # Build PowerShell script for interactive mode
            ps_lines = [
                f"$Host.UI.RawUI.WindowTitle = '{window_title.replace(chr(39), chr(39)+chr(39))}'",
                "$args_list = @()",
            ]
            if skip_permissions:
                ps_lines.append("$args_list += '--yolo'")

            if actual_prompt:
                prompt_file = os.path.join(tmp_dir, "prompt.txt")
                with open(prompt_file, "w", encoding="utf-8") as f:
                    f.write(actual_prompt)
                ps_lines.append(
                    f"$prompt = Get-Content -Path '{prompt_file}' -Raw -Encoding UTF8"
                )
                ps_lines.append("$args_list += $prompt")

            ps_lines.append(f"& '{copilot_executable}' @args_list")
            ps_lines.append("exit $LASTEXITCODE")

            with open(script_file, "w", encoding="utf-8") as f:
                f.write("\n".join(ps_lines))

            ps_exe = _find_powershell()
            proc = subprocess.Popen(
                [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_file],
                cwd=cwd,
                creationflags=CREATE_NEW_CONSOLE,
            )
        else:
            cmd = [copilot_executable]
            if skip_permissions:
                cmd.append("--yolo")
            if actual_prompt:
                cmd.append(actual_prompt)
            proc = subprocess.Popen(cmd, cwd=cwd)

        # Wait indefinitely — user controls when to exit
        proc.wait()
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        response.duration_ms = elapsed_ms
        response.session_id = ""
        response.cost_usd = 0.0
        response.success = proc.returncode == 0
        response.is_error = proc.returncode != 0
        if proc.returncode != 0:
            response.error_message = f"Interactive session exited with code {proc.returncode}"
        response.result_text = "Interactive Copilot session completed"

    except FileNotFoundError:
        response.success = False
        response.is_error = True
        response.error_message = (
            f"Copilot executable not found: '{copilot_executable}'. "
            "Make sure GitHub Copilot CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    finally:
        # Clean up temp directory
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    return response
