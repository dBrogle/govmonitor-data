"""Tests for incremental topic scoring — the path that makes adding a topic cheap.

s4 treats a bill as done only when its output file holds a score for every topic its policy
area targets. These lock in that a partially-scored file is topped up on just the missing
topics, and that merging a top-up never loses prior scores or their provenance.
"""

import json

from services.congress.topics import TOPICS, TOPICS_BY_SLUG
from pipeline.stages.s4_analysis import _existing_scores, missing_topics


def _write(path, scores, **extra):
    path.write_text(json.dumps({
        "congress": 119, "bill_type": "hr", "bill_number": "1",
        "scores": [{"topic_slug": s, "topic_name": s, "score": 0.5, "thoughts": ""} for s in scores],
        **extra,
    }))
    return path


def test_no_file_means_every_targeted_topic_is_missing(tmp_path):
    targeted = TOPICS[:3]
    assert missing_topics(tmp_path / "nope.json", targeted, force=False) == targeted


def test_only_the_unscored_topics_are_returned(tmp_path):
    f = _write(tmp_path / "a.json", ["taxation"])
    targeted = [TOPICS_BY_SLUG["taxation"], TOPICS_BY_SLUG["budget_deficit"]]
    assert [t.slug for t in missing_topics(f, targeted, force=False)] == ["budget_deficit"]


def test_fully_scored_file_needs_no_call(tmp_path):
    targeted = [TOPICS_BY_SLUG["taxation"], TOPICS_BY_SLUG["budget_deficit"]]
    f = _write(tmp_path / "b.json", [t.slug for t in targeted])
    assert missing_topics(f, targeted, force=False) == []


def test_force_rescores_every_targeted_topic(tmp_path):
    targeted = [TOPICS_BY_SLUG["taxation"]]
    f = _write(tmp_path / "c.json", ["taxation"])
    assert missing_topics(f, targeted, force=True) == targeted


def test_retired_slugs_do_not_satisfy_a_live_topic(tmp_path):
    """A legacy file full of government_spending scores must still be scored on budget_deficit —
    this is exactly the case the replace-don't-alias decision depends on."""
    f = _write(tmp_path / "d.json", ["government_spending", "national_debt"])
    targeted = [TOPICS_BY_SLUG["budget_deficit"]]
    assert [t.slug for t in missing_topics(f, targeted, force=False)] == ["budget_deficit"]


def test_unreadable_file_is_treated_as_unscored(tmp_path):
    """A truncated file (killed mid-write) must not be mistaken for a complete one."""
    f = tmp_path / "e.json"
    f.write_text('{"scores": [{"topic_slug"')
    assert missing_topics(f, TOPICS[:2], force=False) == TOPICS[:2]


def test_existing_scores_reads_prior_file_and_index(tmp_path):
    f = _write(tmp_path / "f.json", ["taxation"], llm_model="x-ai/grok-4.3", temperature=0.0)
    prior, by_slug = _existing_scores(f)
    assert prior["llm_model"] == "x-ai/grok-4.3"
    assert set(by_slug) == {"taxation"}


def test_a_topic_the_model_omitted_is_recorded_as_zero(tmp_path, monkeypatch):
    """The prompt tells the model to omit any topic scoring 0. If that omission isn't
    persisted, the bill looks unscored forever and gets re-billed on every run."""
    from pipeline.stages import s4_analysis
    from services.congress.models import BillAnalysis

    asked = [TOPICS_BY_SLUG["budget_deficit"], TOPICS_BY_SLUG["taxation"]]

    class FakeLLM:
        model, temperature = "test-model", 0.0

    class FakeSvc:
        llm_service = FakeLLM()

        def analyze_bill(self, congress, bill_type, bill_number, topics=None):
            # Model answers on taxation only and omits budget_deficit entirely.
            return BillAnalysis(
                congress=congress, bill_type=bill_type, bill_number=str(bill_number),
                text_source="full_text",
                scores=[{"topic_slug": "taxation", "topic_name": "Taxation",
                         "score": 0.6, "thoughts": "cuts rates"}],
            )

        def summarize_bill(self, *a, **k):
            return "a summary"

    monkeypatch.setattr(s4_analysis, "OUTPUT_DIR", tmp_path)
    _, result, error = s4_analysis._analyze_one(FakeSvc(), 119, "HR", "1", "t", asked)

    assert error is None
    by_slug = {s["topic_slug"]: s for s in result["scores"]}
    assert by_slug["budget_deficit"]["score"] == 0.0
    assert by_slug["taxation"]["score"] == 0.6
    # Idempotent: the bill is now complete and won't be queued (or re-billed) again.
    assert missing_topics(tmp_path / "119_HR_1.json", asked, force=False) == []
