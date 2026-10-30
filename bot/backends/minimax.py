"""Subprocess wrapper for MiniMax Code CLI (MiniMax Token Plan).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
from typing import Any

from core.provider_capabilities import parse_model_ref

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult


class MinimaxBackend(Backend):
    """Subprocess adapter for MiniMax Code CLI backed by MiniMax Token Plan."""

    name = "minimax"

    def __init__(
        self,
        binary: str = "minimax",
        timeout_s: int = 20,
        model: str = "",
        cwd: str | None = None,
    ) -> None:
        # Support both 'minimax' and official '@minimax-ai/code' CLI binary 'mcode'
        if binary == "minimax" and not shutil.which(binary) and shutil.which("mcode"):
            self._binary = "mcode"
        else:
            self._binary = binary
        self._timeout = timeout_s
        self._model = model or os.environ.get("MINIMAX_DISPATCH_MODEL", "MiniMax-M3.1-Flash")
        self._cwd = cwd

    async def health(self) -> HealthResult:
        binary = shutil.which(self._binary)
        if not binary:
            return HealthResult(False, f"{self._binary} not in PATH")
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
            return HealthResult(True, f"minimax {version}")
        except FileNotFoundError as exc:
            return HealthResult(False, str(exc))
        except Exception as exc:  # noqa: BLE001
            return HealthResult(False, repr(exc))

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        _, model_id = parse_model_ref(self._model)
        args: list[str] = [self._binary, "exec"]
        if model_id:
            args += ["--model", model_id]
        args += ["--output-format", "json"]
        args += ["--", envelope.prompt]

        env = {
            **os.environ,
            "CI": "1",
            "NONINTERACTIVE": "1",
        }
        if envelope.thinking_mode and envelope.thinking_mode.lower() != "auto":
            env["KHAVIS_THINKING_MODE"] = envelope.thinking_mode
            env["MINIMAX_THINKING_MODE"] = envelope.thinking_mode
        api_key = os.environ.get("MINIMAX_API_KEY")
        if api_key:
            env["MINIMAX_API_KEY"] = api_key

        run_cwd = self._cwd or envelope.workspace_dir or None
        exec_kwargs: dict[str, Any] = {"env": env}
        if run_cwd and os.path.isdir(run_cwd):
            exec_kwargs["cwd"] = run_cwd

        t0 = time.perf_counter()
        err_detail = ""
        stdout = ""
        stderr = ""
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
                rc = proc.returncode if proc.returncode is not None else -1
                stdout = stdout_b.decode(errors="replace").strip()
                stderr = stderr_b.decode(errors="replace").strip()
            except TimeoutError:
                await _kill(proc)
                err_detail = f"timeout after {self._timeout}s"
        except FileNotFoundError as exc:
            err_detail = str(exc)
        except Exception as exc:  # noqa: BLE001
            err_detail = repr(exc)

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if rc != 0 or err_detail:
            err_msg = err_detail or stderr or stdout or f"process exited with {rc}"
            if run_cwd or (rc != 0 and not err_detail):
                from ._http_fallback import (  # noqa: PLC0415
                    call_upstream_llm_fallback,
                    has_any_upstream_key,
                )

                if has_any_upstream_key():
                    loop = asyncio.get_running_loop()
                    fb = await loop.run_in_executor(
                        None,
                        call_upstream_llm_fallback,
                        envelope.prompt,
                        self._model,
                        "minimax",
                        30,
                        run_cwd,
                        True,
                        envelope.thinking_mode,
                        envelope.thinking_parameter,
                    )
                    if fb.get("ok"):
                        return BackendResult(
                            backend="minimax",
                            ok=True,
                            text=str(fb["text"]),
                            latency_ms=int((time.perf_counter() - t0) * 1000),
                            model=str(fb.get("model") or self._model),
                            tokens_in=int(fb.get("tokens_in") or 0),
                            tokens_out=int(fb.get("tokens_out") or 0),
                        )
            return BackendResult(
                backend="minimax",
                ok=False,
                error=err_msg,
                latency_ms=latency_ms,
                model=self._model,
            )

        text = stdout
        tokens_in, tokens_out = 0, 0
        with contextlib.suppress(Exception):
            data = json.loads(stdout)
            if isinstance(data, dict):
                text = data.get("text") or data.get("content") or data.get("response") or stdout
                usage = data.get("usage") or {}
                tokens_in = int(usage.get("prompt_tokens") or 0)
                tokens_out = int(usage.get("completion_tokens") or 0)

        return BackendResult(
            backend="minimax",
            ok=True,
            text=text,
            latency_ms=latency_ms,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(Exception):
        await proc.wait()
