"""Unit tests for K.H.A.V.I.S. Unified Web Workbench (web/workbench.py).

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from scripts.status_server import Handler
from web import workbench as _wb_module
from web.workbench import (
    get_agent_fleet_status,
    list_deliverable_artifacts,
    read_artifact_content,
    render_workbench_html,
)


@pytest.fixture(autouse=True)
def _isolate_workbench_settings(tmp_path, monkeypatch):
    """測試期間不得改到真實的 .env 與 config/workbench_catalog.json。

    模型切換、服務商新增/刪除都會寫入這兩個檔案；改寫到 tmp_path，
    並在結束時還原 os.environ，避免污染使用者的實際設定。
    騰訊雲 K.A.V.I.S. 的健康探測也一併 stub，避免測試對外發出網路請求。
    """
    monkeypatch.setattr(_wb_module, "_CATALOG_PATH", tmp_path / "workbench_catalog.json")
    monkeypatch.setattr(_wb_module, "_persist_env_updates", lambda updates: None)
    monkeypatch.setattr(
        _wb_module,
        "_tencent_kavis_probe_cached",
        lambda: {"ok": False, "endpoint": "http://probe-stub", "detail": "stubbed"},
    )
    monkeypatch.setenv("CLAUDE_DISPATCH_MODEL", "gemini4")
    monkeypatch.setenv("CODEX_DISPATCH_MODEL", "kimi-k3.1-pro")
    saved_env = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved_env)


def test_render_workbench_html() -> None:
    html_content = render_workbench_html()
    assert "<!DOCTYPE html>" in html_content
    assert "K.H.A.V.I.S." in html_content
    assert "Workbench" in html_content
    assert "Claude Code" in html_content
    assert "Codex" in html_content
    assert "Antigravity" in html_content
    assert "MiniMax Code" in html_content
    assert "opencode" in html_content
    assert "MiniMax-M3.1-Flash" in html_content
    assert "glm-5.3-flashx" in html_content
    assert "GPT GO" in html_content
    assert "model-select-claude" in html_content
    assert "model-settings-panel" in html_content
    assert "attachment-input" in html_content
    assert "thinking-mode" in html_content
    assert "loadThinkingCapabilities" in html_content
    assert "thinking_parameter" in html_content


def test_get_agent_fleet_status() -> None:
    fleet = get_agent_fleet_status()
    assert len(fleet) >= 6
    ids = [a["id"] for a in fleet]
    assert "claude" in ids
    assert "codex" in ids
    assert "antigravity" in ids
    assert "minimax" in ids
    assert "opencode" in ids
    assert "khavis" in ids
    # kavis-tencent 已併入 khavis：單一 K.A.V.I.S. Agent，不再有獨立遠端節點項目
    assert "kavis-tencent" not in ids

    claude_node = next(a for a in fleet if a["id"] == "claude")
    assert claude_node["model"] == "gemini-4-argon"
    codex_node = next(a for a in fleet if a["id"] == "codex")
    assert codex_node["model"] == "kimi-k3.1-pro"

    # 合併後的 K.A.V.I.S. Agent：本地 LangGraph 團隊 + 騰訊雲同名節點狀態
    khavis_node = next(a for a in fleet if a["id"] == "khavis")
    assert khavis_node["installed"] is True
    assert khavis_node["status"] == "ready"
    assert khavis_node["name"] == "K.A.V.I.S. Team"
    assert "Tencent" in khavis_node["version"]  # stub 探測 → Offline (stubbed)
    assert "openclaw" in khavis_node["desc"].lower()


def test_list_deliverable_artifacts() -> None:
    artifacts = list_deliverable_artifacts()
    assert isinstance(artifacts, list)
    assert len(artifacts) > 0
    # Should find at least our stress test script or docker compose
    names = [a["name"] for a in artifacts]
    assert any("壓力測試" in n or "Docker" in n or "投影片" in n for n in names)


def test_read_artifact_content() -> None:
    # 1. Existing file
    res = read_artifact_content("docker-compose.yml")
    assert res["ok"] is True
    assert "khavis-status" in res["content"]
    assert res["filename"] == "docker-compose.yml"

    # 2. Non-existent file (blocked by whitelist)
    res_none = read_artifact_content("non_existent_file_xyz.txt")
    assert res_none["ok"] is False
    assert "not found" in res_none["error"].lower() or "access denied" in res_none["error"].lower()

    # 3. Path traversal security protection
    res_denied = read_artifact_content("../../../etc/passwd")
    assert res_denied["ok"] is False
    assert "access denied" in res_denied["error"].lower()


class DummyStatusHandler(Handler):
    def __init__(self, path: str, method: str = "GET", body: bytes = b"") -> None:
        self.path = path
        self.command = method
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.responses: list[tuple[int, bytes, str]] = []

    def _send(
        self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"
    ) -> None:
        self.responses.append((status, body, content_type))


def test_manus_console_routes_are_protected_and_proxy_tasks(monkeypatch) -> None:
    monkeypatch.setenv("KHAVIS_CONSOLE_TOKEN", "console-token")

    unauthorized = DummyStatusHandler("/api/manus/status")
    unauthorized.do_GET()
    assert unauthorized.responses[0][0] == 401

    wb_unauth = DummyStatusHandler(
        "/api/workbench/dispatch",
        method="POST",
        body=b'{"prompt":"hi"}',
    )
    wb_unauth.do_POST()
    assert wb_unauth.responses[0][0] == 401
    assert "Console Token" in json.loads(wb_unauth.responses[0][1])["detail"]

    class FakeManusClient:
        configured = True
        base_url = "https://api.manus.ai"

        def create_task(self, message, *, agent_profile, interactive_mode):
            assert message == "檢查 WQ 狀態"
            assert agent_profile == "max-high"
            assert interactive_mode is True
            return {"ok": True, "task_id": "task-123", "task_title": "WQ"}

        def task_detail(self, task_id):
            assert task_id == "task-123"
            return {"ok": True, "task": {"id": task_id, "status": "running"}}

        def list_messages(self, task_id, *, limit):
            assert task_id == "task-123"
            assert limit == 10
            return {"ok": True, "task_id": task_id, "messages": [], "next_cursor": None}

        def send_message(self, task_id, message, *, agent_profile):
            assert task_id == "task-123"
            assert message == "繼續"
            assert agent_profile == "standard"
            return {"ok": True, "task_id": task_id, "request_id": "req-1"}

    monkeypatch.setattr("core.manus_client.ManusClient", FakeManusClient)

    status = DummyStatusHandler("/api/manus/status")
    status.headers["X-KHAVIS-Console-Token"] = "console-token"
    status.do_GET()
    assert status.responses[0][0] == 200
    assert json.loads(status.responses[0][1])["configured"] is True

    create = DummyStatusHandler(
        "/api/manus/tasks",
        method="POST",
        body='{"message":"檢查 WQ 狀態","agent_profile":"max-high","interactive_mode":true}'.encode(),
    )
    create.headers["X-KHAVIS-Console-Token"] = "console-token"
    create.do_POST()
    assert create.responses[0][0] == 200
    assert json.loads(create.responses[0][1])["task"]["task_id"] == "task-123"

    detail = DummyStatusHandler("/api/manus/tasks/task-123")
    detail.headers["X-KHAVIS-Console-Token"] = "console-token"
    detail.do_GET()
    assert json.loads(detail.responses[0][1])["task"]["status"] == "running"

    messages = DummyStatusHandler("/api/manus/tasks/task-123/messages?limit=10")
    messages.headers["X-KHAVIS-Console-Token"] = "console-token"
    messages.do_GET()
    assert messages.responses[0][0] == 200

    follow_up = DummyStatusHandler(
        "/api/manus/tasks/task-123/messages",
        method="POST",
        body='{"message":"繼續","agent_profile":"standard"}'.encode(),
    )
    follow_up.headers["X-KHAVIS-Console-Token"] = "console-token"
    follow_up.do_POST()
    assert json.loads(follow_up.responses[0][1])["task_id"] == "task-123"


def test_status_server_workbench_endpoints(monkeypatch) -> None:
    # 0. GET /  (一頁式宣傳與 Console Token 登入入口頁)
    h_root = DummyStatusHandler("/")
    h_root.do_GET()
    assert len(h_root.responses) == 1
    root_status, root_body, root_ctype = h_root.responses[0]
    assert root_status == 200
    assert "text/html" in root_ctype
    root_html = root_body.decode("utf-8")
    assert "KHAVIS" in root_html
    assert "handleConsoleLogin" in root_html
    assert "github.com/kiddhsu5/KHAVIS" in root_html
    assert "/workbench" in root_html

    # 1. GET /workbench  (現行介面 = 選項 C 任務卡片流)
    h_wb = DummyStatusHandler("/workbench")
    h_wb.do_GET()
    assert len(h_wb.responses) == 1
    status, body, ctype = h_wb.responses[0]
    assert status == 200
    assert "text/html" in ctype
    wb_body = body.decode("utf-8")
    assert "K.H.A.V.I.S." in wb_body
    assert 'id="c-task-stream"' in wb_body
    assert "manus-console" in wb_body
    assert "manusCreateTask" in wb_body
    assert "X-KHAVIS-Console-Token" in wb_body
    for zone_label in (
        "歷史談話",
        "資料庫",
        "額度、設定",
        "對話串",
        "輸入框",
        "Agent 狀態",
        "Using 檔案",
    ):
        assert zone_label in wb_body
    assert 'id="c-agent-status-list"' in wb_body
    # 首頁只留「Agent 狀態」「資料庫」按鈕，管理操作都搬到獨立專頁
    assert 'id="c-db-list"' not in wb_body
    assert "cOpenPage('agents')" in wb_body
    assert "cOpenPage('artifacts')" in wb_body
    # 專頁上必須真的有可操作的表單（新增服務商、模型新增/刪除、資料庫搜尋）
    # 服務商模型已改為「從供應商抓取清單 → 勾選要呈現的模型」，不再全文列舉輸入框。
    for element_id in (
        "c-provider-form-box",
        "c-prov-label",
        "c-prov-env",
        "c-prov-key",
        "c-prov-models-box",
        "c-prov-models-list",
        "c-prov-models-status",
        "c-prov-models-count",
        "c-prov-presets",
        "c-providers-crud-grid",
        "c-model-crud-agent",
        "c-model-crud-input",
        "c-artifact-search",
        "c-db-page-count",
        "c-main-container",
        "c-composer-wrap",
    ):
        assert f'id="{element_id}"' in wb_body, element_id
        assert wb_body.count(f'id="{element_id}"') == 1, f"duplicate id: {element_id}"
    assert 'id="c-using-files-host"' in wb_body
    # 抓取清單的函式與 API 必須存在；舊的全列舉文字輸入框必須移除
    assert "cFetchProviderModels" in wb_body
    assert "/api/workbench/provider_models" in wb_body
    assert 'id="c-prov-models"' not in wb_body
    # 全頁視圖（如外掛市集頁）：Agent/資料庫/額度/執行明細都以獨立頁呈現
    assert 'id="c-fullpage"' in wb_body
    assert 'id="c-fullpage-content"' in wb_body
    assert "cOpenTurnDetail" in wb_body
    assert "cCloseFullpage" in wb_body
    # 繽紛色彩層與 FeiBo 式側欄導航
    assert "v-nav" in wb_body
    assert "v-brand-grad" in wb_body
    assert "新工作任務" in wb_body
    assert 'id="c-db-thread-groups"' in wb_body

    # 1a. Agent 狀態與資料庫必須可直接深連結開啟，不能只依賴 SPA 歷史狀態。
    for deep_link in ("/agents", "/database"):
        h_page = DummyStatusHandler(deep_link)
        h_page.do_GET()
        assert h_page.responses[0][0] == 200
        assert "text/html" in h_page.responses[0][2]
        assert 'id="c-main-container"' in h_page.responses[0][1].decode("utf-8")

    # 1b. GET /workbench-legacy  (改版前介面，保留作為回退路徑)
    h_legacy = DummyStatusHandler("/workbench-legacy")
    h_legacy.do_GET()
    assert len(h_legacy.responses) == 1
    status, legacy_body, legacy_ctype = h_legacy.responses[0]
    assert status == 200
    assert "text/html" in legacy_ctype
    assert "Workbench" in legacy_body.decode("utf-8")

    # 2. GET /api/workbench/artifacts
    h_art = DummyStatusHandler("/api/workbench/artifacts")
    h_art.do_GET()
    assert len(h_art.responses) == 1
    status, body, ctype = h_art.responses[0]
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert isinstance(data["artifacts"], list)

    # 3. GET /api/workbench/agents
    h_ag = DummyStatusHandler("/api/workbench/agents")
    h_ag.do_GET()
    assert len(h_ag.responses) == 1
    status, body, ctype = h_ag.responses[0]
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert len(data["agents"]) >= 6

    monkeypatch.setattr(
        _wb_module,
        "get_workbench_capabilities",
        lambda: {
            "ok": True,
            "agents": {
                "codex": {
                    "model": "kimi-k3.1-pro",
                    "thinking_modes": ["high", "xhigh"],
                    "thinking_parameter": "reasoning_effort",
                }
            },
            "providers": [],
            "model_options": {},
        },
    )
    h_caps = DummyStatusHandler("/api/workbench/capabilities")
    h_caps.do_GET()
    assert h_caps.responses[0][0] == 200
    caps_data = json.loads(h_caps.responses[0][1].decode("utf-8"))
    assert caps_data["agents"]["codex"]["thinking_modes"] == ["high", "xhigh"]

    # 4. POST /api/workbench/dispatch with empty prompt -> 400
    h_post_empty = DummyStatusHandler(
        "/api/workbench/dispatch", method="POST", body=b'{"prompt":""}'
    )
    h_post_empty.do_POST()
    assert len(h_post_empty.responses) == 1
    status, body, _ = h_post_empty.responses[0]
    assert status == 400

    # 5. POST /api/workbench/dispatch with valid prompt and mocked dispatch
    from unittest.mock import AsyncMock, patch

    from bot.models import BackendResult, DispatchEnvelope, DispatchReport

    mock_report = DispatchReport(
        envelope=DispatchEnvelope(chat_id=1, prompt="test prompt"),
        results=[
            BackendResult(backend="minimax", ok=True, text="Response from MiniMax", latency_ms=120)
        ],
        consensus="Response from MiniMax",
        consensus_source="minimax",
    )
    with patch("bot.dispatch.DispatchRouter.dispatch", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = mock_report
        h_post_valid = DummyStatusHandler(
            "/api/workbench/dispatch",
            method="POST",
            body=b'{"prompt":"test prompt","agents":["minimax"]}',
        )
        h_post_valid.do_POST()
        assert len(h_post_valid.responses) == 1
        status, body, _ = h_post_valid.responses[0]
        assert status == 200
        res_data = json.loads(body.decode("utf-8"))
        assert res_data["ok"] is True
        assert len(res_data["results"]) == 1
        assert res_data["results"][0]["backend"] == "minimax"

    # 5a. Invalid thinking mode syntax and sensitive attachment are rejected.
    h_bad_thinking = DummyStatusHandler(
        "/api/workbench/dispatch",
        method="POST",
        body=b'{"prompt":"test","thinking_mode":"high;rm -rf /"}',
    )
    h_bad_thinking.do_POST()
    assert h_bad_thinking.responses[0][0] == 400

    h_bad_attachment = DummyStatusHandler(
        "/api/workbench/dispatch",
        method="POST",
        body=b'{"prompt":"test","attachments":[{"name":".env","data_base64":"aGVsbG8="}]}',
    )
    h_bad_attachment.do_POST()
    assert h_bad_attachment.responses[0][0] == 400

    # 6. GET & POST /api/workbench/models (adjustable model settings)
    h_mod_get = DummyStatusHandler("/api/workbench/models")
    h_mod_get.do_GET()
    assert h_mod_get.responses[0][0] == 200
    mod_data = json.loads(h_mod_get.responses[0][1].decode("utf-8"))
    assert mod_data["ok"] is True
    assert "claude" in mod_data["models"]

    h_mod_post = DummyStatusHandler(
        "/api/workbench/models",
        method="POST",
        body=b'{"models":{"claude":"glm-5.3-flashx","antigravity":"antigravity-flash"}}',
    )
    h_mod_post.do_POST()
    assert h_mod_post.responses[0][0] == 200
    mod_updated = json.loads(h_mod_post.responses[0][1].decode("utf-8"))
    assert mod_updated["models"]["claude"] == "glm-5.3-flashx"
    assert mod_updated["models"]["antigravity"] == "antigravity-flash"

    # Restore defaults via reset=True
    h_mod_reset = DummyStatusHandler(
        "/api/workbench/models",
        method="POST",
        body=b'{"reset":true}',
    )
    h_mod_reset.do_POST()
    assert h_mod_reset.responses[0][0] == 200
    mod_reset = json.loads(h_mod_reset.responses[0][1].decode("utf-8"))
    assert mod_reset["models"]["claude"] == "gemini-4-argon"
    assert mod_reset["models"]["antigravity"] == "antigravity-pro"


def test_fetch_provider_model_list(monkeypatch) -> None:
    from web.workbench import fetch_provider_model_list

    # 1. 無效 env key → ValueError
    with pytest.raises(ValueError):
        fetch_provider_model_list({"env": "9BAD ENV!"})

    # 2. 非 http/https base_url → ValueError（SSRF 防護）
    with pytest.raises(ValueError):
        fetch_provider_model_list({"env": "DEEPSEEK_API_KEY", "base_url": "file:///etc/passwd"})

    # 3. 遮罩金鑰應被忽略，不送去當真金鑰
    with pytest.raises(ValueError):
        fetch_provider_model_list({"env": "!!"})

    # 4. 正常路徑：mock 上層 _fetch_provider，確認只回 model id 清單
    def fake_fetch(spec, values, timeout_s=5.0):  # noqa: ANN001, ARG001
        return {
            "env": spec["env"],
            "label": "Stub Provider",
            "configured": True,
            "reachable": True,
            "status": "ok",
            "models": [
                {"id": "z-model", "ref": "X::z-model"},
                {"id": "a-model", "ref": "X::a-model"},
                {"id": "a-model", "ref": "X::a-model"},  # 重複應去重
            ],
            "fetched_at": "2026-10-29T00:00:00+00:00",
        }

    monkeypatch.setattr("core.provider_capabilities._fetch_provider", fake_fetch)
    res = fetch_provider_model_list(
        {"env": "STUB_API_KEY", "base_url": "https://stub.example/v1", "api_key": "sk-stub-123"}
    )
    assert res["ok"] is True
    assert res["status"] == "ok"
    assert res["models"] == ["a-model", "z-model"]  # 排序 + 去重
    assert res["count"] == 2

    # 5. 供應商不可達 → ok=False，誠實帶回 status
    def unreachable_fetch(spec, values, timeout_s=5.0):  # noqa: ANN001, ARG001
        return {
            "env": spec["env"],
            "label": spec["env"],
            "configured": False,
            "reachable": False,
            "status": "not_configured",
            "models": [],
        }

    monkeypatch.setattr("core.provider_capabilities._fetch_provider", unreachable_fetch)
    res2 = fetch_provider_model_list({"env": "STUB_API_KEY"})
    assert res2["ok"] is False
    assert res2["status"] == "not_configured"
    assert res2["models"] == []


def test_provider_models_endpoint(monkeypatch) -> None:
    # POST /api/workbench/provider_models：無效 env → 400
    h_bad = DummyStatusHandler(
        "/api/workbench/provider_models",
        method="POST",
        body=b'{"env":"bad env!"}',
    )
    h_bad.do_POST()
    assert h_bad.responses[0][0] == 400

    # 正常路徑：mock 抓取函式 → 200 + id 清單
    monkeypatch.setattr(
        _wb_module,
        "fetch_provider_model_list",
        lambda payload: {
            "ok": True,
            "env": payload.get("env"),
            "label": "Stub",
            "reachable": True,
            "status": "ok",
            "models": ["m1", "m2"],
            "count": 2,
            "fetched_at": None,
        },
    )
    h_ok = DummyStatusHandler(
        "/api/workbench/provider_models",
        method="POST",
        body=b'{"env":"STUB_API_KEY"}',
    )
    h_ok.do_POST()
    assert h_ok.responses[0][0] == 200
    data = json.loads(h_ok.responses[0][1].decode("utf-8"))
    assert data["ok"] is True
    assert data["models"] == ["m1", "m2"]


def test_get_database_overview_groups_by_thread() -> None:
    """資料庫依對話串主題歸檔：同檔多輪歸最新串、找不到來源的落入未歸檔。"""
    from web.workbench import get_database_overview

    history_items = [
        {
            "thread": "t-old",
            "prompt": "舊對話：產出報告初稿",
            "project": "proj-a",
            "ts": 1000.0,
            "dispatched_at": "2026-10-29 10:00:00",
            "output_files": [{"path": "docs/report.md", "full_path": "/ws/docs/report.md", "status": "A", "size": "1 KB"}],
        },
        {
            "thread": "t-new",
            "prompt": "新對話：修正報告並補充 PDF 版本\n第二行",
            "project": "proj-a",
            "ts": 2000.0,
            "dispatched_at": "2026-10-29 11:00:00",
            "output_files": [
                {"path": "docs/report.md", "full_path": "/ws/docs/report.md", "status": "M", "size": "2 KB"},
                {"path": "docs/report.pdf", "full_path": "/ws/docs/report.pdf", "status": "A", "size": "300 KB"},
            ],
        },
    ]
    res = get_database_overview(history_items=history_items)

    # 兩個對話串，t-new 在前（依 ts 排序）
    assert [t["thread"] for t in res["threads"]] == ["t-new", "t-old"]
    t_new = res["threads"][0]
    # 同檔多輪只歸最新串
    assert [f["path"] for f in t_new["files"]] == ["docs/report.md", "docs/report.pdf"]
    assert t_new["count"] == 2
    # 標題取 prompt 摘要且不含換行
    assert "\n" not in t_new["title"]
    # badge 依副檔名
    badges = {f["path"]: f["badge"] for f in t_new["files"]}
    assert badges["docs/report.md"] == "MD"
    assert badges["docs/report.pdf"] == "PDF"

    # 歷史紀錄沒有 output_files 時，thread 不應出現
    empty = get_database_overview(history_items=[{"thread": "t-x", "prompt": "沒有產出", "ts": 5.0}])
    assert all(t["thread"] != "t-x" for t in empty["threads"])


def test_agent_models_set_and_fetch(monkeypatch) -> None:
    """模型勾選：set 持久化為權威清單、fetch 回供應商即時清單 + 當前勾選。"""
    # 1. set：勾選清單持久化
    h_set = DummyStatusHandler(
        "/api/workbench/agent_models",
        method="POST",
        body=b'{"action":"set","agent":"claude","models":["ZHIPUAI_API_KEY::glm-5.3-flashx","JUAPI_API_KEY::gemini-4-argon"]}',
    )
    h_set.do_POST()
    assert h_set.responses[0][0] == 200
    updated = json.loads(h_set.responses[0][1].decode("utf-8"))
    assert "ZHIPUAI_API_KEY::glm-5.3-flashx" in updated["options"]["claude"]
    assert "JUAPI_API_KEY::gemini-4-argon" in updated["options"]["claude"]

    # 2. 再確認：capabilities 動態清單不得復活未勾選模型（此處以 config 直接驗證持久層）
    from web.workbench import get_workbench_models_config

    cfg = get_workbench_models_config()
    assert "ZHIPUAI_API_KEY::glm-5.3-flashx" in cfg["options"]["claude"]

    # 3. set：空清單 → 400
    h_empty = DummyStatusHandler(
        "/api/workbench/agent_models",
        method="POST",
        body=b'{"action":"set","agent":"claude","models":[]}',
    )
    h_empty.do_POST()
    assert h_empty.responses[0][0] == 400

    # 4. fetch：mock 供應商抓取
    def fake_discover(specs, values, timeout_s=6.0):  # noqa: ANN001, ARG001
        return [{
            "env": specs[0]["env"], "label": "Stub", "status": "ok",
            "models": [
                {"id": "glm-a", "ref": "ZHIPUAI_API_KEY::glm-a", "provider_label": "Stub"},
                {"id": "glm-b", "ref": "ZHIPUAI_API_KEY::glm-b", "provider_label": "Stub"},
            ],
        }]

    monkeypatch.setattr("core.provider_capabilities.discover_provider_capabilities", fake_discover)
    h_fetch = DummyStatusHandler(
        "/api/workbench/agent_models",
        method="POST",
        body=b'{"action":"fetch","agent":"claude"}',
    )
    h_fetch.do_POST()
    assert h_fetch.responses[0][0] == 200
    data = json.loads(h_fetch.responses[0][1].decode("utf-8"))
    assert data["ok"] is True
    assert data["agent"] == "claude"
    assert [m["model"] for m in data["models"]] == ["glm-a", "glm-b"]
    assert isinstance(data["selected"], list)

    # 5. 未知 Agent → 400
    h_bad = DummyStatusHandler(
        "/api/workbench/agent_models",
        method="POST",
        body=b'{"action":"fetch","agent":"nope"}',
    )
    h_bad.do_POST()
    assert h_bad.responses[0][0] == 400


def test_database_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        _wb_module,
        "get_database_overview",
        lambda: {
            "threads": [{"thread": "t1", "title": "T", "project": "p", "dispatched_at": "", "ts": 1.0, "count": 1,
                         "files": [{"name": "a.md", "path": "a.md", "full_path": "/ws/a.md", "status": "A",
                                    "size": "", "mtime": "", "icon": "fa-file-alt", "badge": "MD"}]}],
            "ungrouped": [],
            "total": 1,
        },
    )
    h = DummyStatusHandler("/api/workbench/database")
    h.do_GET()
    assert h.responses[0][0] == 200
    data = json.loads(h.responses[0][1].decode("utf-8"))
    assert data["ok"] is True
    assert data["total"] == 1
    assert data["threads"][0]["thread"] == "t1"


def test_fleet_has_kavis_rename_and_merged_tencent() -> None:
    """Agent 改名 K.A.V.I.S.；騰訊雲節點併入同一 agent（拓撲不再有獨立卡片）。"""
    html = render_workbench_html()

    # 拓撲 Node 6 維持 K.A.V.I.S. 單一節點，副標題標示合併身份
    assert ">K.A.V.I.S.</div>" in html
    assert ">K.H.A.V.I.S.</div>" not in html
    assert "LangGraph × 騰訊雲節點" in html
    # Node 7 已移除：不再有獨立的 kavis-tencent 拓撲卡片
    assert 'id="node-card-kavis-tencent"' not in html
    assert 'value="kavis-tencent"' not in html


def test_workspace_and_worktree_endpoints() -> None:
    # 1. GET /api/workbench/workspace
    h_ws_get = DummyStatusHandler("/api/workbench/workspace")
    h_ws_get.do_GET()
    assert h_ws_get.responses[0][0] == 200
    ws_data = json.loads(h_ws_get.responses[0][1].decode("utf-8"))
    assert ws_data["ok"] is True
    assert "shared_dir" in ws_data
    assert ws_data["isolation_mode"] in ("shared", "worktree")

    # 2. Switch isolation_mode to worktree and back to shared
    h_ws_wt = DummyStatusHandler(
        "/api/workbench/workspace",
        method="POST",
        body=b'{"isolation_mode":"worktree","active_project":"shared-app"}',
    )
    h_ws_wt.do_POST()
    assert h_ws_wt.responses[0][0] == 200
    wt_data = json.loads(h_ws_wt.responses[0][1].decode("utf-8"))
    assert wt_data["isolation_mode"] == "worktree"

    import time

    from core.workspace_manager import merge_worktree_to_shared, prepare_agent_cwd

    wt_dir = prepare_agent_cwd("claude", isolation_mode="worktree", project_name="shared-app")
    (wt_dir / "hello_agent.txt").write_text(
        f"created in claude worktree at {time.time()}\n", encoding="utf-8"
    )

    merge_res = merge_worktree_to_shared("claude", project_name="shared-app", auto_commit=True)
    assert merge_res["ok"] is True
    assert "hello_agent.txt" in merge_res["copied_files"]

    # Switch back to shared mode
    h_ws_shared = DummyStatusHandler(
        "/api/workbench/workspace",
        method="POST",
        body=b'{"isolation_mode":"shared"}',
    )
    h_ws_shared.do_POST()
    assert json.loads(h_ws_shared.responses[0][1].decode("utf-8"))["isolation_mode"] == "shared"


def test_custom_agent_registration_and_execution() -> None:
    import asyncio

    from bot.backends.custom import CustomAgentBackend
    from bot.models import DispatchEnvelope
    from core.workspace_manager import prepare_agent_cwd

    # 1. Register a custom CLI agent using /bin/echo
    reg_body = json.dumps(
        {
            "id": "echo_agent",
            "name": "Echo Test Agent",
            "binary": "/bin/echo",
            "args_template": '["CustomAgentExecuted:", "{model}", "{prompt}"]',
            "default_model": "echo-v1",
            "options": ["echo-v1", "echo-v2"],
            "plan": "Self-Hosted CLI",
        }
    ).encode("utf-8")
    h_reg = DummyStatusHandler("/api/workbench/agents/custom", method="POST", body=reg_body)
    h_reg.do_POST()
    assert h_reg.responses[0][0] == 200
    reg_data = json.loads(h_reg.responses[0][1].decode("utf-8"))
    assert reg_data["ok"] is True
    assert reg_data["agent"]["id"] == "echo_agent"

    # 2. Verify custom agent appears in fleet and models config
    fleet = get_agent_fleet_status()
    assert any(a["id"] == "echo_agent" and a.get("custom") is True for a in fleet)

    # 3. Execute CustomAgentBackend inside shared workspace directory
    shared_cwd = prepare_agent_cwd("echo_agent", isolation_mode="shared", project_name="shared-app")
    backend = CustomAgentBackend(reg_data["agent"], model="echo-v2", cwd=str(shared_cwd))
    res = asyncio.run(backend.run(DispatchEnvelope(chat_id=1, prompt="hello workspace")))
    assert res.ok is True
    assert "CustomAgentExecuted:" in res.text
    assert "echo-v2" in res.text
    assert "hello workspace" in res.text

    # 4. Delete custom agent
    del_body = json.dumps({"action": "delete", "id": "echo_agent"}).encode("utf-8")
    h_del = DummyStatusHandler("/api/workbench/agents/custom", method="POST", body=del_body)
    h_del.do_POST()
    assert h_del.responses[0][0] == 200
    del_data = json.loads(h_del.responses[0][1].decode("utf-8"))
    assert del_data["deleted"] is True


def test_setup_wizard_endpoint() -> None:
    h_setup = DummyStatusHandler("/api/workbench/setup")
    h_setup.do_GET()
    assert h_setup.responses[0][0] == 200
    setup_data = json.loads(h_setup.responses[0][1].decode("utf-8"))
    assert setup_data["ok"] is True
    assert "providers" in setup_data
    assert "cli_tools" in setup_data
    assert "deploy_commands" in setup_data
    juapi = next(p for p in setup_data["providers"] if p["env"] == "JUAPI_API_KEY")
    assert juapi["label"] == "JUAPI TokenRouter API Key"
    assert juapi["base_url_env"] == "JUAPI_BASE_URL"
    assert juapi["models"] == ["gemini-4-argon", "kimi-k3.1-pro"]


def test_collaborative_orchestrator_relay_and_next_actions() -> None:
    from unittest.mock import AsyncMock, patch

    from bot.models import BackendResult, DispatchEnvelope, DispatchReport

    call_idx = 0

    async def fake_dispatch(envelope: DispatchEnvelope) -> DispatchReport:
        nonlocal call_idx
        call_idx += 1
        target = (envelope.only or ["antigravity"])[0]
        return DispatchReport(
            envelope=envelope,
            results=[
                BackendResult(
                    backend=target,
                    ok=True,
                    text=f"Step {call_idx} executed by {target}",
                    latency_ms=80,
                )
            ],
            consensus=f"Step {call_idx} executed by {target}",
            consensus_source=target,
        )

    with patch("bot.dispatch.DispatchRouter.dispatch", new_callable=AsyncMock) as mock_disp:
        mock_disp.side_effect = fake_dispatch
        body = json.dumps(
            {
                "prompt": "Build and verify health script",
                "agents": ["antigravity", "codex"],
                "collab_mode": "relay",
                "previous_context": {
                    "prompt": "Initial check",
                    "consensus": "Tailscale is active",
                },
            }
        ).encode("utf-8")
        h_relay = DummyStatusHandler("/api/workbench/dispatch", method="POST", body=body)
        h_relay.do_POST()
        assert h_relay.responses[0][0] == 200
        data = json.loads(h_relay.responses[0][1].decode("utf-8"))
        assert data["ok"] is True
        assert data["collab_mode"] == "relay"
        assert len(data["results"]) == 2
        assert len(data["collaboration_steps"]) == 2
        assert len(data["next_actions"]) >= 2
        assert "Step 2 executed by codex" in data["consensus"]
        assert "review_details" in data


def test_agent_skills_and_result_review_split(tmp_path) -> None:
    from bot.models import BackendResult
    from core.agent_skills import execute_skill
    from core.collab_orchestrator import _split_result_and_review
    from web.workbench_c import render_workbench_html_c

    # 1. Test scaffold_project, edit_file, git_ops, run_tests
    out_scaf = execute_skill(
        {"tool": "scaffold_project", "remote_url": "https://github.com/kiddhsu5/KHAVIS.git"},
        cwd=str(tmp_path),
    )
    assert "[OK]" in out_scaf
    assert (tmp_path / "README.md").is_file()
    assert (tmp_path / "LICENSE").is_file()
    assert (tmp_path / "CONTRIBUTING.md").is_file()
    assert (tmp_path / "scripts" / "run.sh").is_file()
    assert (tmp_path / "tests" / "test_app.py").is_file()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").is_file()

    out_git = execute_skill({"tool": "git_ops", "action": "remote"}, cwd=str(tmp_path))
    assert "https://github.com/kiddhsu5/KHAVIS.git" in out_git

    out_edit = execute_skill(
        {
            "tool": "edit_file",
            "path": "README.md",
            "target": "專案簡介",
            "replacement": "專案核心簡介",
        },
        cwd=str(tmp_path),
    )
    assert "[OK]" in out_edit

    out_test = execute_skill({"tool": "run_tests"}, cwd=str(tmp_path))
    assert "exit_code=0" in out_test

    # 2. Test result-first splitting & collapsible review extraction
    sample_raw = (
        "1. **【🏆 最終完成交付成果】**\n已完成所有檔案修改與測試驗證。\n"
        "<!-- KHAVIS_REVIEW_SPLIT -->\n"
        "2. **【🔍 多 Agent 交叉審查與互評摘要】**\nopencode 與 antigravity 比較結果一致。"
    )
    main_res, rev_details = _split_result_and_review(
        sample_raw,
        [BackendResult(backend="opencode", ok=True, text="Done", latency_ms=42)],
        "已完成 2 個 Agent 交叉審查。",
    )
    assert "已完成所有檔案修改與測試驗證" in main_res
    assert "多 Agent 交叉審查與互評摘要" not in main_res
    assert "多 Agent 交叉審查與互評摘要" in rev_details
    assert "opencode" in rev_details

    # 3. Test workbench_c UI has left/right chat layout, collapsible review, and zero zcode
    html_c = render_workbench_html_c()
    assert "c-turn-user" in html_c
    assert "c-turn-agent" in html_c
    assert "c-review-collapse" in html_c
    assert "c-turn-files" in html_c
    assert "cOutputFilesHtml" in html_c
    assert "cToggleAdvanced" in html_c
    assert "opencode" in html_c
    assert "zcode" not in html_c.lower()

    # 4. Test _collect_output_files deduplicates output files across shared and per-agent diffs
    from scripts.status_server import _collect_output_files

    files = _collect_output_files(
        {
            "shared_changed_files": [
                {"path": "docs/report.md", "full_path": "/ws/docs/report.md", "status": "A", "size": "1.2 KB"}
            ],
            "results": [
                {
                    "cwd": "/ws",
                    "changed_files": [
                        {"path": "docs/report.md", "status": "A"},
                        {"path": "src/main.py", "status": "M", "size": "0.8 KB"},
                    ],
                }
            ],
        }
    )
    assert [f["path"] for f in files] == ["docs/report.md", "src/main.py"]
    assert files[1]["full_path"] == "/ws/src/main.py"

    # 5. Test default-displayed output download panel between 以此任務接續 and 專案檔案 Diff
    assert "c-output-download-panel" in html_c
    assert "/api/workbench/artifacts/download" in html_c
    from web.workbench import resolve_downloadable_artifact

    resolved = resolve_downloadable_artifact("README.zh-TW.md")
    assert resolved is not None and resolved.name == "README.zh-TW.md"
    assert resolve_downloadable_artifact(".env") is None

    # 6. Verify all inline <script> blocks in rendered HTML have valid JS syntax
    import re
    import shutil
    import subprocess

    if shutil.which("node"):
        for idx, script_body in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html_c, re.S)):
            if not script_body.strip():
                continue
            js_file = tmp_path / f"script_{idx}.js"
            js_file.write_text(script_body, encoding="utf-8")
            chk = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
            assert chk.returncode == 0, f"JS syntax error in script {idx}: {chk.stderr}"
