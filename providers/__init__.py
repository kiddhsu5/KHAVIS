"""Provider plugin package — auto-discovers concrete plugins from this dir.

Importing this package does NOT auto-load ``.env``. Callers that need
``SURFACE_IP`` / ``*_API_KEY`` should call ``core.env.load_env()`` first.
"""

from __future__ import annotations
