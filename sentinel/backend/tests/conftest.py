"""conftest.py — Ensure the backend root is on sys.path for all tests.

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
