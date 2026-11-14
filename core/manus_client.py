"""Small, dependency-free client for the Manus API v2 task endpoints."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_ALLOWED_PROFILES = {
    "standard",
    "lite",
    "max",
    "max-medium",
    "max-high",
    "max-extra",
    "max-ultra",
}
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_MESSAGE_CHARS = 20000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ManusAPIError(RuntimeError):
    """Safe, structured error raised for local validation or Manus API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "manus_api_error",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.request_id = request_id

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": f"https://khavis.khorus.tech/errors/{self.code}",
            "title": self.code.replace("_", " ").title(),
            "status": self.status_code,
            "detail": self.message,
        }
        if self.request_id:
            result["request_id"] = self.request_id
        return result


class ManusClient:
    """Call Manus API v2 without exposing the API key to callers."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.manus.ai",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else _load_api_key()).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def create_task(
        self,
        message: str,
        *,
        agent_profile: str = "standard",
        interactive_mode: bool = True,
    ) -> dict[str, Any]:
        self._validate_message(message)
        self._validate_profile(agent_profile)
        payload = {
            "message": {"content": [{"type": "text", "text": message, "visibility": "visible"}]},
            "agent_profile": agent_profile,
            "interactive_mode": bool(interactive_mode),
            "share_visibility": "private",
        }
        return self._request("POST", "/v2/task.create", payload=payload)

    def task_detail(self, task_id: str) -> dict[str, Any]:
        task_id = self._validate_task_id(task_id)
        return self._request("GET", "/v2/task.detail", query={"task_id": task_id})

    def list_messages(self, task_id: str, *, limit: int = 50) -> dict[str, Any]:
        task_id = self._validate_task_id(task_id)
        if not 1 <= limit <= 200:
            raise ManusAPIError(
                "limit must be between 1 and 200",
                status_code=400,
                code="invalid_argument",
            )
        return self._request(
            "GET",
            "/v2/task.listMessages",
            query={"task_id": task_id, "order": "desc", "limit": str(limit)},
        )

    def send_message(
        self,
        task_id: str,
        message: str,
        *,
        agent_profile: str | None = None,
    ) -> dict[str, Any]:
        task_id = self._validate_task_id(task_id)
        self._validate_message(message)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "message": {"content": [{"type": "text", "text": message, "visibility": "visible"}]},
        }
        if agent_profile is not None:
            self._validate_profile(agent_profile)
            payload["agent_profile"] = agent_profile
        return self._request("POST", "/v2/task.sendMessage", payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ManusAPIError(
                "Manus API is not configured on this server",
                status_code=503,
                code="manus_not_configured",
            )

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "K.H.A.V.I.S.-Manus-Bridge/1.0")
        request.add_header("x-manus-api-key", self.api_key)
        if body is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                status_code = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            raw = exc.read(_MAX_RESPONSE_BYTES + 1)
            return self._raise_upstream(raw, int(exc.code))
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ManusAPIError(
                "Manus API is temporarily unreachable",
                status_code=502,
                code="manus_unreachable",
            ) from None

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ManusAPIError(
                "Manus API response is too large",
                status_code=502,
                code="manus_response_too_large",
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ManusAPIError(
                "Manus API returned an invalid JSON response",
                status_code=502,
                code="manus_invalid_response",
            ) from None
        if not isinstance(data, dict):
            raise ManusAPIError(
                "Manus API returned an invalid response shape",
                status_code=502,
                code="manus_invalid_response",
            )
        if not data.get("ok", status_code < 400):
            return self._raise_upstream(raw, status_code)
        return data

    @staticmethod
    def _raise_upstream(raw: bytes, status_code: int) -> dict[str, Any]:
        code = "manus_api_error"
        message = "Manus API request failed"
        request_id: str | None = None
        try:
            data = json.loads(raw.decode("utf-8"))
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                message = str(error.get("message") or message)
            if isinstance(data, dict):
                request_id = str(data.get("request_id") or "") or None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        safe_message = re.sub(
            r"(?i)(api[_-]?key|authorization|bearer)\s*[=:]\s*\S+", "[redacted]", message
        )
        if status_code == 401:
            safe_message = "Manus API rejected the server credential"
            code = "manus_unauthorized"
        elif status_code == 429:
            safe_message = "Manus API rate limit reached; retry later"
            code = "manus_rate_limited"
        raise ManusAPIError(
            safe_message[:240],
            status_code=status_code if 400 <= status_code <= 599 else 502,
            code=code,
            request_id=request_id,
        )

    @staticmethod
    def _validate_message(message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ManusAPIError("message cannot be empty", status_code=400, code="invalid_argument")
        if len(message) > _MAX_MESSAGE_CHARS:
            raise ManusAPIError(
                f"message exceeds {_MAX_MESSAGE_CHARS} characters",
                status_code=400,
                code="invalid_argument",
            )

    @staticmethod
    def _validate_profile(agent_profile: str) -> None:
        if agent_profile not in _ALLOWED_PROFILES:
            raise ManusAPIError(
                "unsupported agent_profile", status_code=400, code="invalid_argument"
            )

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise ManusAPIError("invalid task_id", status_code=400, code="invalid_argument")
        return task_id


def _load_api_key() -> str:
    direct = os.getenv("MANUS_API_KEY", "").strip()
    if direct:
        return direct
    path = os.getenv("MANUS_API_KEY_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
