"""Fixtures for the system layer.

These talk to a running stack over real HTTP — no TestClient, no dependency
overrides, no in-process shortcuts. Bring it up first:

    docker compose exec -T db psql -U postgres -c \\
        "SELECT 'CREATE DATABASE ai_note_taker_system'
         WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='ai_note_taker_system')\\gexec"
    docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

If the stack isn't reachable the whole layer skips rather than fails — an
absent environment is not a broken build.
"""

import os
from collections.abc import Generator

import httpx
import pytest

BASE_URL = os.environ.get("SYSTEM_BASE_URL", "http://localhost:8001")
STUB_URL = os.environ.get("SYSTEM_STUB_URL", "http://localhost:9000")


def _reachable(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2.0).status_code < 500
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_stack() -> None:
    if not _reachable(f"{BASE_URL}/health"):
        pytest.skip(f"No stack at {BASE_URL} — see this module's docstring", allow_module_level=True)
    if not _reachable(f"{STUB_URL}/__stub/requests"):
        pytest.skip(
            f"No LLM stub at {STUB_URL} — start the stack with docker-compose.test.yml, "
            "otherwise these tests would hit the real OpenAI API",
            allow_module_level=True,
        )


@pytest.fixture
def api() -> Generator[httpx.Client, None, None]:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        yield client


@pytest.fixture
def stub() -> Generator[httpx.Client, None, None]:
    """The stub's own control API — lets a test read back what the stack
    actually sent to the model."""
    with httpx.Client(base_url=STUB_URL, timeout=10.0) as client:
        client.delete("/__stub/requests")
        yield client


@pytest.fixture
def conversation(api: httpx.Client) -> Generator[str, None, None]:
    """A real conversation, deleted afterwards so the system database doesn't
    accumulate rows across runs."""
    conversation_id = api.post("/conversations").json()["id"]
    yield conversation_id
    api.delete(f"/conversations/{conversation_id}")
