"""Subprocess wrapper for Command Code CLI (cmd) backed by taste-1 + open models.

Command Code (https://commandcode.ai) is a coding agent that learns coding
preferences via its taste-1 neuro-symbolic model. This backend wraps the
``cmd`` binary for headless one-shot dispatch, mirroring the pattern of
Claude Code / Codex / OpenCode backends.

Install: ``npm i -g command-code`` (binary name: ``cmd``)

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from typing import Any

from core.provider_capabilities import parse_model_ref

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult


class CommandCodeBackend(Backend):
    """Subprocess adapter for Command Code CLI (``cmd -p``)."""

    name = "cmdcode"

    def __init__(
        self,
        binary: str = "cmd",
        timeout_s: int = 180,
        model: str = "",
        cwd: str | None = None,
    ) -> None:
        self._binary = binary
        self._timeout = timeout_s
        self._model = model or os.environ.get(
            "COMMAND_CODE_DISPATCH_MODEL", "anthropic/claude-sonnet-4"
        )
        self._cwd = cwd

    async def health(self) -> HealthResult:
        binary = shutil.which(self._binary)
        if not binary:
            return HealthResult(False, f"{self._binary} not in PATH (npm i -g command-code)")
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            except TimeoutError:
                await _kill(proc)
                return HealthResult(False, "version check timed out")
            if proc.returncode != 0:
                return HealthResult(False, f"exit={proc.returncode}")
            version = (stdout.decode(errors="replace").strip().splitlines() or ["?"])[-1]
            return HealthResult(True, f"cmd {version}")
        except FileNotFoundError as exc:
            return HealthResult(False, str(exc))
        except Exception as exc:  # noqa: BLE001
            return HealthResult(False, repr(exc))

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        _, model_id = parse_model_ref(self._model)
        exe = shutil.which(self._binary) or self._binary
        args: list[str] = [exe, "-p"]
        if model_id:
            args += ["--model", model_id]
        args += ["--", envelope.prompt]

        env = {**os.environ, "CI": "1", "NONINTERACTIVE": "1"}
        run_cwd = self._cwd or envelope.workspace_dir or None
        exec_kwargs: dict[str, Any] = {"env": env}
        if run_cwd and os.path.isdir(run_cwd):
            exec_kwargs["cwd"] = run_cwd

        t0 = time.perf_counter()
        err_detail = ""
        stdout = ""
        rc = -1
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **exec_kwargs,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
                rc = proc.returncode if rc == -1 else rc
                stdout = stdout_b.decode(errors="replace").strip()
                stderr_b.decode(errors="replace")
            except TimeoutError:
                await _kill(proc)
                err_detail = f"timeout after {self._timeout}s"
        except FileNotFoundError as exc:
            err_detail = str(exc)
        except Exception as exc:  # noqa: BLE001
            err_detail = repr(exc)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        if rc != 0 or err_detail:
            return BackendResult(
                backend=self.name,
                ok=False,
                error=err_detail or f"process exited with {rc}",
                latency_ms=latency_ms,
                model=self._model,
            )

        return BackendResult(
            backend=self.name,
            ok=bool(stdout),
            text=stdout or "Command Code returned empty output",
            latency_ms=latency_ms,
            model=self._model,
        )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(Exception):
        await proc.wait()
