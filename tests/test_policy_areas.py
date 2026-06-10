"""Tests for policy-area → topic targeting (pure logic, no LLM/network).

These lock in the prefilter+targeting behavior: known areas map to the right topics, areas
with no matching topic skip the bill, and anything unknown falls back to ALL topics so a bill
is never silently dropped.
"""

from services.congress.topics import TOPICS, TOPICS_BY_SLUG
from services.congress.policy_areas import POLICY_AREA_TOPICS, topics_for_policy_area


def _slugs(policy_area):
    return [t.slug for t in topics_for_policy_area(policy_area, TOPICS)]


def test_all_mapped_slugs_are_real_topics():
    """Every slug in the mapping must exist in topics.py (guards against typos/renames)."""
    bad = {s for slugs in POLICY_AREA_TOPICS.values() for s in slugs if s not in TOPICS_BY_SLUG}
    assert not bad, f"mapping references unknown topic slugs: {bad}"


def test_known_area_maps_to_expected_topics():
    # Set comparison — ordering is asserted separately below.
    assert set(_slugs("Taxation")) == {"taxation", "government_spending", "national_debt"}
    assert _slugs("Immigration") == ["immigration"]
    assert _slugs("Environmental Protection") == ["climate_environment"]


def test_returned_topics_preserve_topics_order_and_are_configs():
    # Order should follow TOPICS, not the mapping's listing order.
    result = topics_for_policy_area("Taxation", TOPICS)
    assert all(t in TOPICS for t in result)
    order = [t.slug for t in TOPICS if t.slug in {"taxation", "government_spending", "national_debt"}]
    assert [t.slug for t in result] == order


def test_irrelevant_area_skips_bill():
    # Empty mapping → [] → caller skips the bill entirely.
    assert topics_for_policy_area("Animals", TOPICS) == []
    assert topics_for_policy_area("Congress", TOPICS) == []
    assert topics_for_policy_area("Commerce", TOPICS) == []


def test_unknown_or_missing_area_falls_back_to_all_topics():
    # Never silently drop a bill we can't classify.
    assert _slugs("Some Brand New Policy Area") == [t.slug for t in TOPICS]
    assert _slugs(None) == [t.slug for t in TOPICS]
    assert _slugs("") == [t.slug for t in TOPICS]


def test_defense_bills_are_not_scored_on_lgbtq():
    """The Barry-Moore guard at the targeting layer: a defense bill is never even asked about
    LGBTQ, so a buried provision cannot produce an LGBTQ score."""
    defense_topics = _slugs("Armed Forces and National Security")
    assert "military_defense" in defense_topics
    assert "lgbtq_rights" not in defense_topics
