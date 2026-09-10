"""Root pytest configuration.

Its presence puts the repo root on ``sys.path``, so both ``tests/`` and
``backend/tests/`` can ``import backend.*`` without the per-file path juggling
those suites used to do.

The default run is offline: no Neo4j, no API keys, no server. Anything needing a
live service must be marked ``@pytest.mark.integration`` (see pytest.ini).
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_configure(config):
    """Point the offline suite at values that fail fast rather than hang.

    Service singletons are lazy (backend/shared/utils/lazy.py), so importing the
    app touches nothing. If a test does reach for a real connection, we want a
    quick refusal instead of a long DNS/TCP timeout — that turns "this test
    secretly needs a database" into an immediate, obvious failure.
    """
    os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:9")
    os.environ.setdefault("NEO4J_USERNAME", "neo4j")
    os.environ.setdefault("NEO4J_PASSWORD", "test-not-a-real-password")
    os.environ.setdefault("ENVIRONMENT", "test")
