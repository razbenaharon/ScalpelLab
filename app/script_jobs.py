"""Single-process script job runner for NiceGUI pages."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class JobSnapshot:
    active: bool
    name: str | None
    status: str
    returncode: int | None
    started_at: float | None
    finished_at: float | None
    logs: list[str] = field(default_factory=list)


class ScriptJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._name: str | None = None
        self._status = "idle"
        self._returncode: int | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._logs: list[str] = []

    def start(self, name: str, args: list[str], env: dict[str, str] | None = None) -> bool:
        with self._lock:
            if self._process and self._process.poll() is None:
                return False
            self._name = name
            self._status = "running"
            self._returncode = None
            self._started_at = time.time()
            self._finished_at = None
            self._logs = [f"$ {' '.join(args)}"]

            merged_env = os.environ.copy()
            merged_env["PYTHONIOENCODING"] = "utf-8"
            if env:
                merged_env.update(env)

            self._process = subprocess.Popen(
                args,
                cwd=str(PROJECT_ROOT),
                env=merged_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()
            return True

    def _reader(self) -> None:
        process = self._process
        if not process:
            return
        assert process.stdout is not None
        try:
            for line in process.stdout:
                self._append(line.rstrip("\r\n"))
            returncode = process.wait()
        except Exception as exc:
            self._append(f"[ERROR] {exc}")
            returncode = process.poll()

        with self._lock:
            self._returncode = returncode
            self._finished_at = time.time()
            if self._status == "cancelling":
                self._status = "cancelled"
            elif returncode == 0:
                self._status = "success"
            else:
                self._status = "failed"
            self._logs.append(f"[exit code] {returncode}")

    def _append(self, line: str) -> None:
        with self._lock:
            self._logs.append(line)
            if len(self._logs) > 2000:
                self._logs = self._logs[-2000:]

    def cancel(self) -> None:
        with self._lock:
            process = self._process
            if not process or process.poll() is not None:
                return
            self._status = "cancelling"
            self._logs.append("[INFO] Cancellation requested.")
            process.terminate()

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            active = bool(self._process and self._process.poll() is None)
            return JobSnapshot(
                active=active,
                name=self._name,
                status=self._status,
                returncode=self._returncode,
                started_at=self._started_at,
                finished_at=self._finished_at,
                logs=list(self._logs),
            )


manager = ScriptJobManager()


def python_script_args(script: str, extra: list[str]) -> list[str]:
    return [sys.executable, str(PROJECT_ROOT / script), *extra]
