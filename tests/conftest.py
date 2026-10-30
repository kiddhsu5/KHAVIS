"""Pytest configuration & shared fixtures for K.H.A.V.I.S. tests.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is on ``sys.path`` so ``providers`` and ``core``
# resolve regardless of how pytest is invoked.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------
def _has_env(name: str) -> bool:
    return bool(os.getenv(name))


# Mapping of pool name → required API key env var (mirrors pools.yaml).
POOL_ENV_KEYS: dict[str, str | None] = {
    "MiniMax-M3": "MiniMax_API_KEY",
    "GLM-5.3": "ZHIPUAI_API_KEY",
    "Google-Gemini": "GOOGLE_API_KEY",
    "NVIDIA-Cloud": "NVIDIA_API_KEY",
    "DeepSeek-V4-Pro": "BYTEDANCE_API_KEY",
    "DeepSeek-V4.1-Flash": "BYTEDANCE_API_KEY",
    "GLM-5.3-Flash": "BYTEDANCE_API_KEY",
    "OpenRouter-Free": "OPENROUTER_API_KEY",
    "OpenAI-API": "OPENAI_API_KEY",
    "Claude-API": "ANTHROPIC_API_KEY",
    "Ollama-Mac": None,
    "Ollama-Surface": "SURFACE_IP",
}


@pytest.fixture
def require_api_key():
    """Factory fixture: skip the test if the named env var is unset."""
    return _has_env


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT
