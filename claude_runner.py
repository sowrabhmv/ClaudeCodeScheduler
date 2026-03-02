"""Claude CLI subprocess wrapper with JSON parsing and session chaining."""

import subprocess
import json
import time
import os
from dataclasses import dataclass, field
from typing import Optional


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


def run_prompt(
    prompt_text: str,
    working_dir: str = ".",
    session_id: Optional[str] = None,
    claude_executable: str = "claude",
    timeout_seconds: int = 600,
    skip_permissions: bool = True,
) -> PromptResponse:
    """
    Run a single prompt via the Claude CLI.

    First prompt:  claude -p "prompt" --dangerously-skip-permissions --output-format json
    Subsequent:    claude -p "prompt" --resume <session_id> --dangerously-skip-permissions --output-format json

    If prompt_text starts with 'file:', the referenced .md file is read at runtime.
    """
    # Resolve file references
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
        response.result_json = raw

        if proc.returncode != 0 and not raw:
            response.success = False
            response.is_error = True
            response.error_message = proc.stderr.strip() or f"Exit code {proc.returncode}"
            return response

        # Try parsing JSON response
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
                # result can be a list of content blocks
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
            # If we got non-JSON output, treat stdout as plain text result
            if proc.returncode == 0:
                response.success = True
                response.result_text = raw
            else:
                response.success = False
                response.is_error = True
                response.error_message = raw or proc.stderr.strip()

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
