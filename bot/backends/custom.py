"""Execution backend for user-defined Custom Agents (CLI or HTTP API).

Supports:
1. Native CLI execution inside the shared project workspace (`cwd`) using the user's
   configured `binary` and `args_template` (`{model}`, `{prompt}`).
2. OpenAI/Anthropic-compatible HTTP API execution (either when `type == "http"` or as
   an automatic fallback when the custom CLI binary is not yet installed on the host).

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import time
import urllib.request
from typing import Any

from core.provider_capabilities import parse_model_ref

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult


class CustomAgentBackend(Backend):
    """Dynamic adapter for a user-registered custom agent."""

    def __init__(
        self,
        spec: dict[str, Any],
        timeout_s: int = 180,
        model: str = "",
        cwd: str | None = None,
    ) -> None:
        self.name = str(spec.get("id") or "custom")
        self._spec = spec
        self._timeout = timeout_s
        self._model = model or str(spec.get("model") or "MiniMax-M3.1-Flash")
        self._cwd = cwd

    @property
    def model(self) -> str:
        return self._model

    async def health(self) -> HealthResult:
        agent_type = str(self._spec.get("type") or "cli").lower()
        if agent_type == "http":
            return HealthResult(True, f"custom-http ({self._model})")
        binary = str(self._spec.get("binary") or self.name).split()[0]
        found = shutil.which(binary)
        if found:
            return HealthResult(True, f"{binary} ({found})")
        return HealthResult(True, f"{binary} (API hybrid fallback ready)")

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        agent_type = str(self._spec.get("type") or "cli").lower()
        binary_raw = str(self._spec.get("binary") or self.name).strip()
        bin_parts = shlex.split(binary_raw) if binary_raw else [self.name]
        exe_path = shutil.which(bin_parts[0]) if bin_parts else None

        run_cwd = self._cwd or envelope.workspace_dir or None

        if agent_type == "cli" and exe_path:
            return await self._run_cli(
                bin_parts,
                envelope.prompt,
                run_cwd,
                envelope.thinking_mode,
                envelope.thinking_parameter,
            )
        return await self._run_http_fallback(
            envelope.prompt,
            run_cwd,
            envelope.thinking_mode,
            envelope.thinking_parameter,
        )

    async def _run_cli(
        self,
        bin_parts: list[str],
        prompt: str,
        run_cwd: str | None,
        thinking_mode: str | None = None,
        thinking_parameter: str | None = None,
    ) -> BackendResult:
        _, model_id = parse_model_ref(self._model)
        template = str(self._spec.get("args_template") or "--model {model} -- {prompt}").strip()
        raw_tokens = shlex.split(template) if template else ["--", "{prompt}"]
        built_args: list[str] = list(bin_parts)
        prompt_injected = False
        for tok in raw_tokens:
            if "{prompt}" in tok:
                built_args.append(tok.replace("{prompt}", prompt))
                prompt_injected = True
            elif "{model}" in tok:
                built_args.append(tok.replace("{model}", model_id))
            else:
                built_args.append(tok)
        if not prompt_injected:
            built_args += ["--", prompt]

        env = {**os.environ, "CI": "1", "NONINTERACTIVE": "1"}
        if thinking_mode and thinking_mode.lower() != "auto":
            env["KHAVIS_THINKING_MODE"] = thinking_mode
            if thinking_parameter:
                env["KHAVIS_THINKING_PARAMETER"] = thinking_parameter
        api_key_env = str(self._spec.get("api_key_env") or "").strip()
        if api_key_env and os.environ.get(api_key_env):
            key_val = os.environ[api_key_env]
            env["OPENAI_API_KEY"] = key_val
            env["ANTHROPIC_API_KEY"] = key_val
        base_url = str(self._spec.get("base_url") or "").strip()
        if base_url:
            env["OPENAI_BASE_URL"] = base_url

        exec_kwargs: dict[str, Any] = {"env": env}
        if run_cwd and os.path.isdir(run_cwd):
            exec_kwargs["cwd"] = run_cwd

        t0 = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *built_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **exec_kwargs,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return BackendResult(
                    backend=self.name,
                    ok=False,
                    error=f"timeout after {self._timeout}s",
                    model=self._model,
                )
        except Exception as exc:  # noqa: BLE001
            return BackendResult(
                backend=self.name,
                ok=False,
                error=repr(exc),
                model=self._model,
            )

        latency_ms = int((time.perf_counter() - t0) * 1000)
        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()
        if proc.returncode != 0:
            return BackendResult(
                backend=self.name,
                ok=False,
                error=stderr or stdout or f"exit={proc.returncode}",
                latency_ms=latency_ms,
                model=self._model,
            )
        return BackendResult(
            backend=self.name,
            ok=True,
            text=stdout,
            latency_ms=latency_ms,
            model=self._model,
        )

    async def _run_http_fallback(
        self,
        prompt: str,
        run_cwd: str | None,
        thinking_mode: str | None = None,
        thinking_parameter: str | None = None,
    ) -> BackendResult:
        """Execute custom agent via OpenAI-compatible Chat Completions API."""
        t0 = time.perf_counter()
        _, model_id = parse_model_ref(self._model)
        model_lower = model_id.lower()
        api_key_env = str(self._spec.get("api_key_env") or "").strip()
        api_key = (
            (os.environ.get(api_key_env) if api_key_env else None)
            or (
                os.environ.get("ZHIPUAI_API_KEY")
                if "glm" in model_lower or "codegeex" in model_lower
                else None
            )
            or os.environ.get("MINIMAX_API_KEY")
            or os.environ.get("ZHIPUAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        base_url = str(self._spec.get("base_url") or "").strip()
        if not base_url:
            if "glm" in model_lower or "codegeex" in model_lower:
                base_url = "https://open.bigmodel.cn/api/coding/paas/v4"
            elif "minimax" in model_lower:
                base_url = "https://api.minimax.io/v1"
            else:
                base_url = os.environ.get("OPENAI_BASE_URL", "https://api.minimax.io/v1")

        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        sys_prompt = (
            str(self._spec.get("system_prompt") or "").strip()
            or f"You are {self._spec.get('name') or self.name}, a specialized coding and engineering agent running in workspace {run_cwd or '.'}. Respond concisely in Traditional Chinese."
        )

        request_body: dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1200,
        }
        if thinking_mode and thinking_mode.lower() != "auto":
            request_body[thinking_parameter or "reasoning_effort"] = thinking_mode
        req_body = json.dumps(request_body).encode("utf-8")

        def _do_request() -> tuple[bool, str, int, int]:
            req = urllib.request.Request(
                endpoint,
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = raw.get("choices") or []
            msg = choices[0].get("message", {}) if choices else {}
            content = msg.get("content") or msg.get("reasoning_content") or ""
            usage = raw.get("usage") or {}
            return (
                True,
                str(content).strip(),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )

        try:
            ok, text, tin, tout = await asyncio.to_thread(_do_request)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return BackendResult(
                backend=self.name,
                ok=ok,
                text=text,
                latency_ms=latency_ms,
                model=self._model,
                tokens_in=tin,
                tokens_out=tout,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return BackendResult(
                backend=self.name,
                ok=False,
                error=f"Custom Agent HTTP error: {exc}",
                latency_ms=latency_ms,
                model=self._model,
            )
