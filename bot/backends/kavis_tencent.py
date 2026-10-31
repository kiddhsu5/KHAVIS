"""Tencent Cloud K.A.V.I.S. backend (openclaw × hermes merged remote node).

自建的遠端常駐節點上由 hermes gateway（Telegram 常駐閘道）與
openclaw（Agent 執行環境）合併運作的 K.A.V.I.S. 節點。這個 backend 讓
本機／雲端 Workbench 能把它納入 Agent 拓撲：健康探測走 HTTP，派工走
選配的 OpenAI 相容 chat/completions 端點。

環境變數（值不寫進 code，一律由 .env 提供）：
- ``KAVIS_TENCENT_ENDPOINT``：健康探測基底 URL（由 KAVIS_TENCENT_ENDPOINT 指定，
  例如 https://your-kavis-node.example.com）。任何 HTTP 回應（含 401/404）都視為節點在線。
- ``KAVIS_TENCENT_DISPATCH_URL``：OpenAI 相容 dispatch 端點（例如
  ``http://<host>:<port>/v1/chat/completions``）。未設定時本節點僅做
  監控與架構呈現，派工會回誠實的「通道未開通」結果，而不是靜默失敗。
- ``KAVIS_TENCENT_API_KEY``：dispatch 端點的 Bearer 金鑰（選配）。
- ``KAVIS_TENCENT_MODEL``：dispatch 使用的模型 ID（預設 MiniMax-M3）。

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult

# 騰訊雲節點的對外服務：khavis 狀態頁／工作台在 443（自簽憑證）。
# hermes gateway 若有獨立 HTTP 埠，用 KAVIS_TENCENT_ENDPOINT 覆蓋即可。
DEFAULT_ENDPOINT = ""  # 由 KAVIS_TENCENT_ENDPOINT 指定；未設定時顯示未配置
DEFAULT_MODEL = "MiniMax-M3"
_HEALTH_TIMEOUT_S = 2.5
_DISPATCH_TIMEOUT_S = 180


def _probe_ssl_context() -> ssl.SSLContext:
    """探測只看「節點是否在線」，不驗證自簽憑證的身分。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def tencent_endpoint() -> str:
    return os.environ.get("KAVIS_TENCENT_ENDPOINT", "").strip() or DEFAULT_ENDPOINT


def dispatch_url() -> str:
    return os.environ.get("KAVIS_TENCENT_DISPATCH_URL", "").strip()


def probe_tencent_kavis(endpoint: str | None = None, timeout_s: float = _HEALTH_TIMEOUT_S) -> dict[str, Any]:
    """HTTP probe of the Tencent K.A.V.I.S. node. Never raises; never leaks keys."""
    url = (endpoint or tencent_endpoint()).rstrip("/")
    if not url:
        return {"ok": False, "endpoint": "", "detail": "endpoint_not_configured"}
    kwargs: dict[str, Any] = {"timeout": timeout_s}
    if url.lower().startswith("https"):
        kwargs["context"] = _probe_ssl_context()
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, **kwargs) as resp:
            code = int(resp.status)
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
    except (OSError, ValueError) as exc:
        return {"ok": False, "endpoint": url, "detail": f"unreachable ({exc.__class__.__name__})"}
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {"ok": True, "endpoint": url, "http_code": code, "latency_ms": latency_ms}


class KavisTencentBackend(Backend):
    """Remote openclaw × hermes node; monitoring always, dispatch when configured."""

    name = "kavis-tencent"

    def __init__(self, model: str = "", endpoint: str = "") -> None:
        self._model = model or os.environ.get("KAVIS_TENCENT_MODEL", "").strip() or DEFAULT_MODEL
        self._endpoint = endpoint or tencent_endpoint()

    @property
    def model(self) -> str:
        return self._model

    async def health(self) -> HealthResult:
        probe = probe_tencent_kavis(self._endpoint)
        if probe.get("ok"):
            wired = "dispatch ready" if dispatch_url() else "monitor-only"
            return HealthResult(
                True,
                f"K.A.V.I.S. online ({probe.get('http_code')} @ {probe.get('endpoint')}, {wired})",
            )
        return HealthResult(False, f"K.A.V.I.S. offline ({probe.get('detail')} @ {probe.get('endpoint')})")

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        t0 = time.perf_counter()
        url = dispatch_url()
        if not url:
            return BackendResult(
                backend=self.name,
                ok=False,
                error="dispatch_not_configured",
                text=(
                    "騰訊雲 K.A.V.I.S.（openclaw × hermes）節點目前在架構中為**監控節點**：\n"
                    f"- 健康探測端點：{self._endpoint}\n"
                    "- 派工通道尚未開通。請在 .env 設定 KAVIS_TENCENT_DISPATCH_URL"
                    "（OpenAI 相容 /chat/completions 端點）後，即可把任務派到該節點。"
                ),
                model=self._model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        api_key = os.environ.get("KAVIS_TENCENT_API_KEY", "").strip()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": envelope.prompt}],
            }
        ).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_DISPATCH_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            return BackendResult(
                backend=self.name,
                ok=False,
                error=f"http_{exc.code}",
                text="騰訊雲 K.A.V.I.S. dispatch 失敗（HTTP {code}）".format(code=exc.code),
                model=self._model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        except (OSError, ValueError) as exc:
            return BackendResult(
                backend=self.name,
                ok=False,
                error=exc.__class__.__name__,
                text=f"騰訊雲 K.A.V.I.S. dispatch 連線失敗：{exc.__class__.__name__}",
                model=self._model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        text = ""
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                text = str(message.get("content") or "")
        if not text and isinstance(payload, dict):
            text = str(payload.get("content") or payload.get("output") or "")
        return BackendResult(
            backend=self.name,
            ok=bool(text),
            error="" if text else "empty_response",
            text=text or "騰訊雲 K.A.V.I.S. 回傳空結果",
            model=str(payload.get("model") or self._model) if isinstance(payload, dict) else self._model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
