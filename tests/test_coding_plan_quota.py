"""Parsers for MiniMax / GLM coding-plan quota payloads."""

from __future__ import annotations

from core.coding_plan_quota import (
    parse_glm_quota,
    parse_minimax_remains,
    refresh_coding_plan_quotas,
)
from core.quota_tracker import QuotaTracker


MINIMAX = {
    "model_remains": [
        {
            "model_name": "general",
            "current_interval_total_count": 0,
            "current_interval_usage_count": 0,
            "current_interval_remaining_percent": 100,
            "current_weekly_remaining_percent": 99,
            "end_time": 1793721600000,
            "weekly_end_time": 1794153600000,
        },
        {"model_name": "video", "current_interval_remaining_percent": 100},
    ]
}

GLM = {
    "success": True,
    "data": {
        "level": "lite",
        "limits": [
            {
                "type": "CREDIT_LIMIT",
                "unit": 3,
                "number": 5,
                "usage": 2000,
                "currentValue": 327,
                "remaining": 1672,
                "percentage": 16,
                "nextResetTime": 1793713695582,
            },
            {
                "type": "CREDIT_LIMIT",
                "unit": 6,
                "number": 1,
                "usage": 10000,
                "currentValue": 7738,
                "remaining": 2261,
                "percentage": 77,
                "nextResetTime": 1793736079956,
            },
        ],
    },
}


def test_minimax_prefers_percent_and_ignores_video() -> None:
    parsed = parse_minimax_remains(MINIMAX)
    assert parsed["ok"] is True
    assert parsed["pool"] == "MiniMax-M3"
    assert parsed["remaining_ratio"] == 0.99
    assert parsed["windows"][0]["label"] == "本段"
    assert parsed["windows"][1]["remaining_ratio"] == 0.99


def test_glm_uses_remaining_over_cap_not_blog_token_shape() -> None:
    parsed = parse_glm_quota(GLM)
    assert parsed["ok"] is True
    assert parsed["plan"] == "GLM Coding Plan · lite"
    assert abs(parsed["windows"][0]["remaining_ratio"] - 1672 / 2000) < 1e-9
    assert parsed["windows"][0]["label"] == "5 小時"
    assert parsed["windows"][1]["label"] == "本週"
    assert parsed["remaining_ratio"] == parsed["windows"][1]["remaining_ratio"]


def test_refresh_records_snapshots_and_skips_missing_keys() -> None:
    tracker = QuotaTracker()
    calls: list[str] = []

    def fake_minimax(key: str) -> dict:
        calls.append(key)
        return parse_minimax_remains(MINIMAX)

    refresh_coding_plan_quotas(
        tracker,
        force=True,
        getenv=lambda name: "mm-key" if name == "MINIMAX_API_KEY" else "",
        query_minimax_fn=fake_minimax,
        query_glm_fn=lambda _key: {"ok": False},
    )
    snaps = tracker.all_snapshots()
    assert calls == ["mm-key"]
    assert snaps["MiniMax-M3"]["remaining_ratio"] == 0.99
    assert snaps["GLM-5.3"]["note"] == "未設定 ZHIPUAI_API_KEY"
    assert "mm-key" not in str(snaps)
