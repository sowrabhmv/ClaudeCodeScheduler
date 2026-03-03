"""Claude CLI subprocess wrapper with JSON parsing and session chaining."""

import subprocess
import json
import time
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Optional

CREATE_NEW_CONSOLE = 0x00000010  # Windows flag for new console window


@dataclass
class PromptResponse:
    success: bool = False
    result_text: str = ""
    result_json: str = ""
    error_message: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    is_error: bool = False


def resolve_prompt_text(prompt_text: str) -> str:
    """
    If prompt_text is a file reference (file:path/to/file.md), read the file
    contents at execution time. Otherwise return the text as-is.
    """
    if prompt_text.startswith("file:"):
        file_path = prompt_text[5:].strip()
        if os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            raise FileNotFoundError(f"Prompt file not found: {file_path}")
    return prompt_text


def _parse_json_response(
    response: PromptResponse,
    raw: str,
    returncode: int,
    stderr: str,
    elapsed_ms: int,
) -> PromptResponse:
    """Parse JSON output from Claude CLI into a PromptResponse."""
    response.result_json = raw

    if returncode != 0 and not raw:
        response.success = False
        response.is_error = True
        response.error_message = stderr.strip() or f"Exit code {returncode}"
        return response

    try:
        data = json.loads(raw)
        response.session_id = data.get("session_id", "")
        response.cost_usd = float(data.get("total_cost_usd", 0) or 0)
        response.duration_ms = int(data.get("duration_ms", elapsed_ms) or elapsed_ms)
        response.is_error = bool(data.get("is_error", False))

        # Extract result text
        result = data.get("result", "")
        if isinstance(result, str):
            response.result_text = result
        elif isinstance(result, list):
            parts = []
            for block in result:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            response.result_text = "\n".join(parts)
        else:
            response.result_text = str(result)

        if response.is_error:
            response.success = False
            response.error_message = response.result_text
        else:
            response.success = True

    except json.JSONDecodeError:
        if returncode == 0:
            response.success = True
            response.result_text = raw
        else:
            response.success = False
            response.is_error = True
            response.error_message = raw or stderr.strip()

    return response


def run_prompt(
    prompt_text: str,
    working_dir: str = ".",
    session_id: Optional[str] = None,
    claude_executable: str = "claude",
    timeout_seconds: int = 600,
    skip_permissions: bool = True,
) -> PromptResponse:
    """
    Run a single prompt via the Claude CLI (headless mode).

    First prompt:  claude -p "prompt" --dangerously-skip-permissions --output-format json
    Subsequent:    claude -p "prompt" --resume <session_id> --dangerously-skip-permissions --output-format json

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

    cmd = [claude_executable, "-p", actual_prompt]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["--output-format", "json"])
    if session_id:
        cmd.extend(["--resume", session_id])

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
        return _parse_json_response(response, raw, proc.returncode, proc.stderr, elapsed_ms)

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
            f"Claude executable not found: '{claude_executable}'. "
            "Make sure Claude CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    return response


def run_prompt_visible(
    prompt_text: str,
    working_dir: str = ".",
    session_id: Optional[str] = None,
    claude_executable: str = "claude",
    timeout_seconds: int = 600,
    skip_permissions: bool = True,
    schedule_name: str = "",
) -> PromptResponse:
    """
    Run a prompt in a visible console window. Output is captured via a temp file.

    A new console window opens with the schedule name in the title bar.
    The user can watch Claude work but cannot interact (read-only).
    JSON output is redirected to a temp file and parsed after completion.
    """
    response = PromptResponse()
    try:
        actual_prompt = resolve_prompt_text(prompt_text)
    except FileNotFoundError as e:
        response.success = False
        response.is_error = True
        response.error_message = str(e)
        return response

    cmd_parts = [claude_executable, "-p", actual_prompt]
    if skip_permissions:
        cmd_parts.append("--dangerously-skip-permissions")
    cmd_parts.extend(["--output-format", "json"])
    if session_id:
        cmd_parts.extend(["--resume", session_id])

    start_ms = time.monotonic_ns() // 1_000_000

    # Create temp file for output capture
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="claude_visible_")
    os.close(tmp_fd)

    try:
        cwd = working_dir if os.path.isdir(working_dir) else "."
        window_title = f"Claude Code - {schedule_name}" if schedule_name else "Claude Code"

        if os.name == "nt":
            # Build a cmd.exe wrapper that sets the window title and redirects output
            escaped_parts = []
            for part in cmd_parts:
                # Escape double quotes inside arguments for cmd.exe
                escaped = part.replace('"', '\\"')
                escaped_parts.append(f'"{escaped}"')
            inner_cmd = " ".join(escaped_parts)
            shell_cmd = f'cmd.exe /c "title {window_title} && {inner_cmd} > "{tmp_path}" 2>&1"'

            proc = subprocess.Popen(
                shell_cmd,
                cwd=cwd,
                creationflags=CREATE_NEW_CONSOLE,
            )
        else:
            # On non-Windows, just run with output redirect
            proc = subprocess.Popen(
                cmd_parts,
                stdout=open(tmp_path, "w"),
                stderr=subprocess.STDOUT,
                cwd=cwd,
            )

        proc.wait(timeout=timeout_seconds)
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        response.duration_ms = elapsed_ms

        # Read output from temp file
        try:
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
        except Exception:
            raw = ""

        return _parse_json_response(response, raw, proc.returncode, "", elapsed_ms)

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
            f"Claude executable not found: '{claude_executable}'. "
            "Make sure Claude CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return response


def run_interactive(
    working_dir: str = ".",
    claude_executable: str = "claude",
    session_id: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    schedule_name: str = "",
) -> PromptResponse:
    """
    Launch Claude in interactive TUI mode in a new console window.

    The user gets full Claude TUI interaction. No -p flag, no --output-format json.
    Uses --session-id for session tracking. No timeout — user controls the session.
    Returns session ID, duration, and exit code.
    """
    response = PromptResponse()

    if session_id is None:
        session_id = str(uuid.uuid4())

    cmd = [claude_executable, "--session-id", session_id]

    # If there's an initial prompt, pass it as a positional argument
    if initial_prompt:
        try:
            actual_prompt = resolve_prompt_text(initial_prompt)
            cmd.append(actual_prompt)
        except FileNotFoundError as e:
            response.success = False
            response.is_error = True
            response.error_message = str(e)
            return response

    start_ms = time.monotonic_ns() // 1_000_000

    try:
        cwd = working_dir if os.path.isdir(working_dir) else "."
        window_title = f"Claude Code - {schedule_name}" if schedule_name else "Claude Code (Interactive)"

        if os.name == "nt":
            # Build cmd.exe wrapper with title
            escaped_parts = []
            for part in cmd:
                escaped = part.replace('"', '\\"')
                escaped_parts.append(f'"{escaped}"')
            inner_cmd = " ".join(escaped_parts)
            shell_cmd = f'cmd.exe /c "title {window_title} && {inner_cmd}"'

            proc = subprocess.Popen(
                shell_cmd,
                cwd=cwd,
                creationflags=CREATE_NEW_CONSOLE,
            )
        else:
            proc = subprocess.Popen(cmd, cwd=cwd)

        # Wait indefinitely — user controls when to exit
        proc.wait()
        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        response.duration_ms = elapsed_ms
        response.session_id = session_id
        response.success = proc.returncode == 0
        response.is_error = proc.returncode != 0
        if proc.returncode != 0:
            response.error_message = f"Interactive session exited with code {proc.returncode}"
        response.result_text = f"Interactive session completed (session: {session_id})"

    except FileNotFoundError:
        response.success = False
        response.is_error = True
        response.error_message = (
            f"Claude executable not found: '{claude_executable}'. "
            "Make sure Claude CLI is installed and the path is correct in Settings."
        )

    except Exception as e:
        response.success = False
        response.is_error = True
        response.error_message = f"Unexpected error: {e}"

    return response


def run_prompt_chain(
    prompts: list[dict],
    working_dir: str = ".",
    claude_executable: str = "claude",
    timeout_seconds: int = 600,
    on_prompt_start=None,
    on_prompt_done=None,
) -> list[PromptResponse]:
    """
    Run a chain of prompts, using --resume to continue the session.

    prompts: list of dicts with keys: id, prompt_order, prompt_text
    on_prompt_start(prompt_dict, index): called before each prompt
    on_prompt_done(prompt_dict, index, response): called after each prompt

    On failure, remaining prompts are marked as skipped (returned with success=False).
    """
    results = []
    session_id = None

    for i, prompt in enumerate(prompts):
        if on_prompt_start:
            on_prompt_start(prompt, i)

        resp = run_prompt(
            prompt_text=prompt["prompt_text"],
            working_dir=working_dir,
            session_id=session_id,
            claude_executable=claude_executable,
            timeout_seconds=timeout_seconds,
        )

        results.append(resp)

        if on_prompt_done:
            on_prompt_done(prompt, i, resp)

        if resp.success and resp.session_id:
            session_id = resp.session_id
        elif not resp.success:
            # Chain failed — mark remaining as skipped
            for j in range(i + 1, len(prompts)):
                skipped = PromptResponse(
                    success=False,
                    error_message="Skipped due to previous prompt failure",
                )
                results.append(skipped)
                if on_prompt_done:
                    on_prompt_done(prompts[j], j, skipped)
            break

    return results
