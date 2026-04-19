"""Shared pytest configuration.

pytest loads this file before it imports any test module. We seed
``SESSION_SECRET`` here so that tests importing :mod:`heardle.api` (which
invokes ``SessionMiddleware`` at app-construction time) succeed without
requiring a fully-populated ``.env`` on the developer's machine.
"""

from __future__ import annotations

import os

# pragma: allowlist secret
os.environ.setdefault("SESSION_SECRET", "test_session_secret_not_used_in_prod")
