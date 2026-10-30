#!/usr/bin/env python3
"""K.A.V.I.S. dispatch adapter — OpenAI-compatible bridge to the hermes agent.

騰訊雲 K.A.V.I.S.（openclaw × hermes）節點的派工橋接器。K.H.A.V.I.S. Workbench
的 ``bot/backends/kavis_tencent.py`` 以 OpenAI 相容協議派工；本程式把
``POST /v1/chat/completions`` 轉成 hermes CLI 的單次無頭呼叫（``hermes -z``），
讓遠端節點的 Agent（含工具、記憶體、SOUL 人格）真正接到任務。

安全模型：
- 只綁 ``127.0.0.1``（預設 18081）。不對外開放；外網本來就連不到。
- 設 ``KAVIS_ADAPTER_TOKEN`` 時，要求 ``Authorization: Bearer <token>``；
  未設時僅接受 localhost 連線。
- 同一時間只跑一個 hermes session（全域鎖序列化），避免與 Telegram gateway
  的 session 狀態互踩。

用法（騰訊雲 VM，user service）：
    KAVIS_ADAPTER_TOKEN=<optional> python3 kavis_adapter.py

Workbench 端 .env：
    KAVIS_TENCENT_DISPATCH_URL=http://127.0.0.1:18081/v1/chat/completions
    # 有設 KAVIS_ADAPTER_TOKEN 才需要：
    # KAVIS_TENCENT_API_KEY=<same token>

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LISTEN_HOST = os.environ.get("KAVIS_ADAPTER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("KAVIS_ADAPTER_PORT", "18081"))
HERMES_BIN = os.environ.get(
    "KAVIS_ADAPTER_HERMES",
    os.environ.get("KAVIS_HERMES_BIN", ""),
)
HERMES_TIMEOUT_S = float(os.environ.get("KAVIS_ADAPTER_TIMEOUT_S", "170"))
MAX_PROMPT_CHARS = int(os.environ.get("KAVIS_ADAPTER_MAX_PROMPT", "100000"))
ADAPTER_TOKEN = os.environ.get("KAVIS_ADAPTER_TOKEN", "").strip()
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/\-]{1,80}$")

_hermes_lock = threading.Lock()


def _flatten_messages(messages: Any) -> str:
    """把 OpenAI messages 陣列攤平成單一 prompt（保留 system/user 角色）。"""
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        content = msg.get("content")
        if isinstance(content, list):
            # OpenAI content parts: [{"type":"text","text":"..."}]
            content = "\n".join(
                str(p.get("text") or "")
                for p in content
                if isinstance(p, dict) and str(p.get("type") or "text") == "text"
            )
        text = str(content or "").strip()
        if not text:
            continue
        if role == "system":
            parts.append(f"[系統指示]\n{text}")
        elif role == "assistant":
            parts.append(f"[前次回覆]\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)[:MAX_PROMPT_CHARS]


def _run_hermes(prompt: str, model: str) -> dict[str, Any]:
    """序列化執行 hermes 單次無頭呼叫。回傳 {text} 或 {error}。"""
    env = dict(os.environ)
    env.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes"))
    cmd = [HERMES_BIN]
    if model and _MODEL_RE.match(model):
        cmd += ["-m", model]
    cmd += ["-z", prompt]
    with _hermes_lock:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=HERMES_TIMEOUT_S,
                env=env,
                cwd=env["HERMES_HOME"],
            )
        except subprocess.TimeoutExpired:
            return {"error": f"hermes timeout after {HERMES_TIMEOUT_S}s"}
        except OSError as exc:
            return {"error": f"hermes launch failed: {exc.__class__.__name__}"}
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        return {"error": f"hermes exit {proc.returncode}: {detail}"}
    if not text:
        return {"error": "hermes returned empty output"}
    return {"text": text}


class Handler(BaseHTTPRequestHandler):
    server_version = "kavis-adapter/1.0"

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not ADAPTER_TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {ADAPTER_TOKEN}"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, {"ok": True, "node": "tencent-kavis", "port": LISTEN_PORT})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/v1/chat/completions":
            self._send(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._send(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": {"message": "invalid json body", "type": "bad_request"}})
            return

        prompt = _flatten_messages(payload.get("messages"))
        if not prompt:
            self._send(
                400,
                {"error": {"message": "messages 內容為空", "type": "bad_request"}},
            )
            return

        model = str(payload.get("model") or "").strip()
        t0 = time.perf_counter()
        result = _run_hermes(prompt, model)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if "error" in result:
            self._send(
                502,
                {
                    "error": {
                        "message": result["error"],
                        "type": "kavis_adapter_error",
                    }
                },
            )
            return

        self._send(
            200,
            {
                "id": f"chatcmpl-kavis-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model or "hermes-default",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result["text"]},
                        "finish_reason": "stop",
                    }
                ],
                "kavis_latency_ms": latency_ms,
            },
        )

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        print(f"[kavis-adapter] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(
        f"[kavis-adapter] listening on http://{LISTEN_HOST}:{LISTEN_PORT} "
        f"(hermes={HERMES_BIN}, timeout={HERMES_TIMEOUT_S}s, "
        f"auth={'token' if ADAPTER_TOKEN else 'localhost-only'})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
