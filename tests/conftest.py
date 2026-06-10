"""Shared pytest fixtures for the data pipeline tests."""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

DATA_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(DATA_ROOT))
load_dotenv(DATA_ROOT / ".env")

# Bulk scoring model for Phase 2 (cheap, 1M context, reliable structured output).
SCORING_MODEL = "google/gemini-2.5-flash"


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: test makes a real LLM API call (needs OPENROUTER_API_KEY)")


def _api_keys() -> list[str]:
    keys = []
    i = 1
    while (k := os.getenv(f"CONGRESS_API_KEY_{i}")) and k != "put_key_here":
        keys.append(k)
        i += 1
    if not keys and (single := os.getenv("CONGRESS_API_KEY")):
        keys.append(single)
    return keys


@pytest.fixture(scope="session")
def scoring_service():
    """A CongressService wired to the Phase 2 scoring model.

    Bill text/summaries for the tested bills are already cached on disk, so no Congress.gov
    calls are made — only the OpenRouter scoring call. Skips the whole module if no key."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_key or openrouter_key == "put_key_here":
        pytest.skip("OPENROUTER_API_KEY not set — skipping LLM scoring tests")

    from services.congress.congress import CongressService
    from services.llm.openrouter import OpenRouterService

    llm = OpenRouterService(api_key=openrouter_key, model=SCORING_MODEL)
    return CongressService(api_key=_api_keys(), llm_service=llm)
