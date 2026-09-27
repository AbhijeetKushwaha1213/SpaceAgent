"""conftest.py — Shared test environment for all SENTINEL tests.

When tests are run from the tests/ directory (e.g. ``python test_models.py``),
the ``backend/`` directory must be on sys.path so that ``from app.api.models
import ...`` and ``from simulation.fault_simulator import ...`` resolve correctly.

This file is automatically loaded by pytest before any test collection, and
is also imported by each test file's ``sys.path`` setup when run standalone.
"""

import os
import sys

# Add the backend/ root (parent of tests/) to sys.path
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# Run the in-process test server in SECURE_DEV_MODE so unit/integration
# suites exercise endpoints without an API key. Production enforcement of
# SENTINEL_API_KEY is verified explicitly by the Phase 14 security tests,
# which construct their own middleware configs.
os.environ.setdefault("SECURE_DEV_MODE", "1")
