"""Core package — capability router, plugin registry, hot reload.

Importing this package does NOT auto-load ``.env`` (see ``core.env``).
Callers that need ``.env`` should call ``core.env.load_env()`` explicitly.
"""

from __future__ import annotations
