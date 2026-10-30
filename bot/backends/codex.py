"""Subprocess wrapper for ``codex exec`` (OpenAI Codex CLI)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import time
from typing import Any

from core.provider_capabilities import parse_model_ref

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult


class CodexBackend(Backend):
    name = "codex"

    def __init__(
        self,
        binary: str = "codex",
        timeout_s: int = 20,
        model: str = "",
        cwd: str | None = None,
    ) -> None:
        self._binary = binary
        self._timeout = timeout_s
        self._model = model or os.environ.get("CODEX_DISPATCH_MODEL", "kimi-k3.1-pro")
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
            return HealthResult(True, f"codex {version}")
        except FileNotFoundError as exc:
            return HealthResult(False, str(exc))
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return HealthResult(False, repr(exc))

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        provider_env, model_id = parse_model_ref(self._model)
        args: list[str] = [
            self._binary,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if model_id:
            args += ["-m", model_id]
        thinking_mode = envelope.thinking_mode
        if (
            thinking_mode
            and thinking_mode.lower() != "auto"
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", thinking_mode)
        ):
            args += ["-c", f"model_reasoning_effort={thinking_mode}"]
        # Try JSON output first; fall back to plain text if the flag is rejected.
        args += ["--json"]
        # ``--`` ends option parsing so a prompt that starts with "-" (or
        # names a subcommand like "resume") is always taken as the prompt.
        args += ["--", envelope.prompt]

        env = {
            **os.environ,
            "CI": "1",
            "NONINTERACTIVE": "1",
        }
        if thinking_mode and thinking_mode.lower() != "auto":
            env["KHAVIS_THINKING_MODE"] = thinking_mode
        # Dynamic cross-provider credential mapping based on selected model
        model_lower = model_id.lower()
        juapi_key = os.environ.get("JUAPI_API_KEY")
        glm_key = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY")
        minimax_key = os.environ.get("MINIMAX_API_KEY")
        if juapi_key and (
            provider_env == "JUAPI_API_KEY" or (not provider_env and _uses_juapi_model(model_lower))
        ):
            env["OPENAI_API_KEY"] = juapi_key
            env["OPENAI_BASE_URL"] = _juapi_openai_base_url()
        elif glm_key and (
            "glm" in model_lower or "flashx" in model_lower or "codegeex" in model_lower
        ):
            env["OPENAI_API_KEY"] = glm_key
            if "OPENAI_BASE_URL" not in env:
                env["OPENAI_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"
        elif minimax_key and "minimax" in model_lower:
            env["OPENAI_API_KEY"] = minimax_key
            if "OPENAI_BASE_URL" not in env:
                env["OPENAI_BASE_URL"] = "https://api.minimax.cn/v1"

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
        except Exception as exc:  # noqa: BLE001 - last-ditch
            err_detail = repr(exc)

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if rc != 0 or err_detail:
            # Retry without --json in case the flag isn't accepted.
            if not err_detail and "--json" in args and "unknown flag" in stderr.lower():
                return await self._run_plain(envelope, latency_ms=latency_ms)
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
                        "codex",
                        30,
                        run_cwd,
                        True,
                        envelope.thinking_mode,
                        envelope.thinking_parameter,
                    )
                    if fb.get("ok"):
                        return BackendResult(
                            backend="codex",
                            ok=True,
                            text=str(fb["text"]),
                            latency_ms=int((time.perf_counter() - t0) * 1000),
                            model=str(fb.get("model") or self._model),
                            tokens_in=int(fb.get("tokens_in") or 0),
                            tokens_out=int(fb.get("tokens_out") or 0),
                        )
            return BackendResult(
                backend="codex",
                ok=False,
                error=err_detail or stderr or f"exit={rc}",
                latency_ms=latency_ms,
                model=self._model,
            )

        text, model, tok_in, tok_out = _parse_codex_output(stdout)
        return BackendResult(
            backend="codex",
            ok=True,
            text=text,
            model=model or self._model,
            tokens_in=tok_in,
            tokens_out=tok_out,
            latency_ms=latency_ms,
        )

    async def _run_plain(self, envelope: DispatchEnvelope, *, latency_ms: int = 0) -> BackendResult:
        _, model_id = parse_model_ref(self._model)
        args = [self._binary, "exec"]
        if model_id:
            args += ["-m", model_id]
        thinking_mode = envelope.thinking_mode
        if (
            thinking_mode
            and thinking_mode.lower() != "auto"
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", thinking_mode)
        ):
            args += ["-c", f"model_reasoning_effort={thinking_mode}"]
        args += ["--", envelope.prompt]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CI": "1"},
            )
            t0 = time.perf_counter()
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError:
                await _kill(proc)
                return BackendResult(
                    backend="codex",
                    ok=False,
                    error=f"timeout after {self._timeout}s",
                    model=self._model,
                )
        except FileNotFoundError as exc:
            return BackendResult(backend="codex", ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - last-ditch
            return BackendResult(backend="codex", ok=False, error=repr(exc))
        elapsed = int((time.perf_counter() - t0) * 1000) + latency_ms
        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()
        if proc.returncode != 0:
            return BackendResult(
                backend="codex",
                ok=False,
                error=stderr or f"exit={proc.returncode}",
                latency_ms=elapsed,
            )
        return BackendResult(
            backend="codex",
            ok=True,
            text=stdout,
            model=self._model,
            latency_ms=elapsed,
        )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill a timed-out child and reap it so it does not linger as a zombie."""
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)


def _uses_juapi_model(model_lower: str) -> bool:
    """Return whether the selected model is explicitly registered to JUAPI."""
    configured = {
        item.strip().lower()
        for item in os.environ.get("JUAPI_MODELS", "").split(",")
        if item.strip()
    }
    return model_lower in configured or model_lower.startswith("kimi-")


def _juapi_openai_base_url() -> str:
    """Normalize JUAPI's generic base URL to the documented `/v1` endpoint."""
    base = (
        os.environ.get("JUAPI_OPENAI_BASE_URL", "").strip()
        or os.environ.get("JUAPI_BASE_URL", "").strip()
        or "https://www.juapi.net/v1"
    ).rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _parse_codex_output(raw: str) -> tuple[str, str, int, int]:
    """Best-effort: many CLI shapes (JSON, JSONL, plain)."""
    raw = (raw or "").strip()
    if not raw:
        return "", "", 0, 0
    # Try the whole blob as JSON first.
    text, model, tin, tout = _try_json_one(raw)
    if text or model or tin or tout:
        return text, model, tin, tout
    # Otherwise scan for JSONL or {"role":"assistant",...} entries.
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        text, model, tin, tout = _try_json_one(line)
        if text:
            return text, model, tin, tout
    return raw, "", 0, 0


def _try_json_one(raw: str) -> tuple[str, str, int, int]:
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return "", "", 0, 0
    if isinstance(data, dict):
        for key in ("text", "content", "output", "message"):
            if key in data and isinstance(data[key], str):
                return data[key], str(data.get("model") or ""), 0, 0
        # Codex sometimes returns {"role":"assistant","content":[...]}
        if data.get("role") == "assistant":
            content = data.get("content")
            if isinstance(content, str):
                return content, "", 0, 0
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                joined = "\n".join(p for p in parts if p)
                return joined, "", 0, 0
        usage = data.get("usage") or {}
        return (
            "",
            str(data.get("model") or ""),
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )
    return "", "", 0, 0
