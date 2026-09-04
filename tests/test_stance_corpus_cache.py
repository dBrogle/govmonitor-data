"""Tests for the stated-positions corpus cache and incremental topic scoring.

Crawling 431 member sites is the slow, fragile, rate-limited half of the stances stage — and
over-parallelising it once got us a site-wide 403. The cache exists so that ADDING A TOPIC
never touches the network. These lock that in, plus the merge that keeps settled stances
stable when the topic set grows.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.congress.topics import TOPICS, TOPICS_BY_SLUG
from services.positions.stances import StatedStance
from pipeline.stages import s6_stances as s6


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(s6, "OUTPUT_DIR", tmp_path / "stances")
    monkeypatch.setattr(s6, "CORPUS_DIR", tmp_path / "corpus")
    (tmp_path / "stances").mkdir()
    (tmp_path / "corpus").mkdir()
    return tmp_path


def _write_corpus(age_days=0, text="x" * 1000):
    s6.corpus_path("XX", 1).write_text(json.dumps({
        "state": "XX", "district": 1, "website": "https://x.house.gov/",
        "fetched_at": (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds"),
        "sources": ["https://x.house.gov/"], "text": text,
    }))


def _write_stances(slugs):
    s6.output_path("XX", 1).write_text(json.dumps({
        "state": "XX", "district": 1,
        "stances": [{"topic_slug": s, "addressed": True, "stated_score": 0.5,
                     "emphasis": 0.5, "quote": "q", "reasoning": "r"} for s in slugs],
    }))


# ── which topics get scored ────────────────────────────────────────────────────────────────

def test_member_with_no_file_needs_every_topic():
    assert s6.missing_topics_for("XX", 1, force=False) == list(TOPICS)


def test_only_newly_added_topics_are_scored():
    """The whole point: adding a topic must not re-score settled ones."""
    _write_stances([t.slug for t in TOPICS if t.slug != "money_in_politics"])
    assert [t.slug for t in s6.missing_topics_for("XX", 1, force=False)] == ["money_in_politics"]


def test_fully_covered_member_is_skipped():
    _write_stances([t.slug for t in TOPICS])
    assert s6.missing_topics_for("XX", 1, force=False) == []


def test_force_rescores_everything():
    _write_stances([t.slug for t in TOPICS])
    assert s6.missing_topics_for("XX", 1, force=True) == list(TOPICS)


# ── the corpus cache ───────────────────────────────────────────────────────────────────────

def test_fresh_corpus_is_reused_without_touching_the_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("gather_member_text was called — the cache should have been used")
    monkeypatch.setattr(s6, "gather_member_text", explode)
    monkeypatch.setattr(s6, "_website", lambda s, d: "https://x.house.gov/")
    _write_corpus(age_days=1)

    _, website, text, sources, err = s6._gather_one(
        {"state": "XX", "district": 1}, max_age_days=30, refresh=False)
    assert err is None and len(text) == 1000 and sources and website


def test_stale_corpus_is_recrawled(monkeypatch):
    monkeypatch.setattr(s6, "_website", lambda s, d: "https://x.house.gov/")
    monkeypatch.setattr(s6, "gather_member_text", lambda url: ("fresh" * 200, ["u"]))
    _write_corpus(age_days=99)
    _, _, text, _, err = s6._gather_one({"state": "XX", "district": 1},
                                        max_age_days=30, refresh=False)
    assert err is None and text.startswith("fresh")


def test_refresh_flag_bypasses_a_fresh_cache(monkeypatch):
    monkeypatch.setattr(s6, "_website", lambda s, d: "https://x.house.gov/")
    monkeypatch.setattr(s6, "gather_member_text", lambda url: ("recrawled" * 100, ["u"]))
    _write_corpus(age_days=0)
    _, _, text, _, err = s6._gather_one({"state": "XX", "district": 1},
                                        max_age_days=30, refresh=True)
    assert err is None and text.startswith("recrawled")


def test_a_successful_crawl_is_persisted_before_scoring(monkeypatch):
    """A crawl must never be wasted — the corpus is written even if scoring later fails."""
    monkeypatch.setattr(s6, "_website", lambda s, d: "https://x.house.gov/")
    monkeypatch.setattr(s6, "gather_member_text", lambda url: ("body" * 300, ["u1", "u2"]))
    s6._gather_one({"state": "XX", "district": 1}, max_age_days=30, refresh=False)
    saved = json.loads(s6.corpus_path("XX", 1).read_text())
    assert saved["text"].startswith("body") and saved["sources"] == ["u1", "u2"]
    assert saved["fetched_at"]


def test_unreachable_site_is_not_cached(monkeypatch):
    """Caching a failure would poison the cache for max_age_days."""
    monkeypatch.setattr(s6, "_website", lambda s, d: "https://x.house.gov/")
    monkeypatch.setattr(s6, "gather_member_text", lambda url: ("", []))
    _, _, _, _, err = s6._gather_one({"state": "XX", "district": 1},
                                     max_age_days=30, refresh=False)
    assert err == "site unreachable"
    assert not s6.corpus_path("XX", 1).exists()


# ── merging ────────────────────────────────────────────────────────────────────────────────

def test_scoring_a_new_topic_keeps_existing_stances(monkeypatch):
    """A fresh crawl can legitimately move a stance a long way, so settled topics must survive
    a topic addition untouched — otherwise every truth score drifts whenever we add one."""
    _write_stances(["taxation"])

    class FakeLLM:
        model, temperature = "test-model", 0.0

    monkeypatch.setattr(s6, "score_stances", lambda llm, text, topics: [
        StatedStance(topic_slug="money_in_politics", addressed=True, emphasis=0.4,
                     stated_score=-0.6, quote="q", reasoning="r")
    ])

    _, result, _ = s6._score_one({"state": "XX", "district": 1}, "https://x.house.gov/",
                                 "text", ["u"], FakeLLM(),
                                 [TOPICS_BY_SLUG["money_in_politics"]])
    by = {s["topic_slug"]: s for s in result["stances"]}
    assert by["taxation"]["stated_score"] == 0.5          # untouched
    assert by["money_in_politics"]["stated_score"] == -0.6  # newly added
    assert by["money_in_politics"]["llm_model"] == "test-model"
