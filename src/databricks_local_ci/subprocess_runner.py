from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EntrypointResult:
    """Result of running a module's entry point as a subprocess."""

    returncode: int
    stdout: str
    stderr: str


def run_entrypoint(
    module: str,
    args: list[str] | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> EntrypointResult:
    """Runs a Python module as a subprocess and captures its result.

    Args:
        module: Dotted module path to execute via `python -m`.
        args: Command-line arguments to pass to the module.
        cwd: Working directory to run the subprocess in.
        timeout: Seconds to wait before raising `subprocess.TimeoutExpired`, or None to wait indefinitely.

    Returns:
        The subprocess's exit code, stdout, and stderr.
    """
    completed = subprocess.run(
        [sys.executable, "-m", module, *(args or [])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=timeout,
    )
    return EntrypointResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
