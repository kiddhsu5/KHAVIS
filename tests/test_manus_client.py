"""Offline tests for the Manus API v2 client."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from core.manus_client import ManusAPIError, ManusClient


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1):
        return self._raw


def test_create_task_sends_v2_payload_and_api_key(monkeypatch):
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response({"ok": True, "task_id": "task-123"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = ManusClient(api_key="secret-key").create_task(
        "檢查 WQ 狀態", agent_profile="max-high", interactive_mode=True
    )

    assert result["task_id"] == "task-123"
    assert seen["url"] == "https://api.manus.ai/v2/task.create"
    assert seen["method"] == "POST"
    assert seen["headers"]["X-manus-api-key"] == "secret-key"
    assert "Authorization" not in seen["headers"]
    assert seen["body"]["agent_profile"] == "max-high"
    assert seen["body"]["share_visibility"] == "private"
    assert seen["timeout"] == 30.0


def test_task_detail_and_messages_encode_task_id(monkeypatch):
    urls: list[str] = []

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        return _Response({"ok": True, "task": {"id": "abc"}, "messages": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ManusClient(api_key="key")
    client.task_detail("agent-default-main_task")
    client.list_messages("agent-default-main_task", limit=10)

    assert "task_id=agent-default-main_task" in urls[0]
    assert "task_id=agent-default-main_task" in urls[1]
    assert "limit=10" in urls[1]
    assert "order=desc" in urls[1]


def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("MANUS_API_KEY", raising=False)
    with pytest.raises(ManusAPIError) as exc_info:
        ManusClient().task_detail("task-123")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "manus_not_configured"
    assert "API Key" not in exc_info.value.message


def test_upstream_unauthorized_and_rate_limit_are_safe(monkeypatch):
    def fake_unauthorized(_request, **_kwargs):
        raise urllib.error.HTTPError(
            "https://api.manus.ai/v2/task.detail",
            401,
            "unauthorized",
            {},
            io.BytesIO(
                b'{"ok":false,"request_id":"req-1","error":{"code":"unauthenticated","message":"api_key=secret-key"}}'
            ),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_unauthorized)
    with pytest.raises(ManusAPIError) as exc_info:
        ManusClient(api_key="secret-key").task_detail("task-123")
    assert exc_info.value.status_code == 401
    assert exc_info.value.message == "Manus API rejected the server credential"
    assert "secret-key" not in exc_info.value.message
    assert exc_info.value.request_id == "req-1"

    def fake_rate_limit(_request, **_kwargs):
        raise urllib.error.HTTPError(
            "https://api.manus.ai/v2/task.detail",
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"error":{"code":"rate_limited"}}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_rate_limit)
    with pytest.raises(ManusAPIError) as rate_exc:
        ManusClient(api_key="key").task_detail("task-123")
    assert rate_exc.value.status_code == 429
    assert rate_exc.value.code == "manus_rate_limited"


def test_validation_rejects_empty_or_unsafe_values():
    client = ManusClient(api_key="key")
    with pytest.raises(ManusAPIError, match="message cannot be empty"):
        client.create_task("  ")
    with pytest.raises(ManusAPIError, match="invalid task_id"):
        client.task_detail("../secret")
    with pytest.raises(ManusAPIError, match="between 1 and 200"):
        client.list_messages("task-1", limit=201)
    with pytest.raises(ManusAPIError, match="unsupported agent_profile"):
        client.send_message("task-1", "hello", agent_profile="unknown")


def test_send_message_sends_follow_up(monkeypatch):
    seen: dict[str, object] = {}

    def fake_urlopen(request, **_kwargs):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"ok": True, "task_id": "task-1"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = ManusClient(api_key="key").send_message(
        "task-1", "繼續整理結果", agent_profile="standard"
    )
    assert result["ok"] is True
    assert seen["url"].endswith("/v2/task.sendMessage")
    assert seen["body"]["task_id"] == "task-1"
    assert seen["body"]["agent_profile"] == "standard"
