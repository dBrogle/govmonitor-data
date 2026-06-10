"""Map a bill's Congress.gov policy area to the topics worth scoring it on.

Congress.gov assigns exactly one policy area per bill (free metadata, already fetched). Phase 2
scoring uses it two ways (see ALIGNMENT_QUALITY_PLAN.md):

  1. Prefilter — a bill whose policy area maps to no topic (post-office namings, internal
     congressional matters, commemorations, animals/sports/arts, finance/commerce regulation
     we have no axis for) is skipped entirely: no LLM call, no spurious scores.
  2. Targeting — for the rest, score only the plausibly-relevant topics instead of all 19.
     Output tokens dominate cost, and it stops the model inventing weak signal on unrelated
     topics (we never ask a defense bill about abortion).

Philosophy: map GENEROUSLY. Excluding a relevant topic loses signal; including a marginal one
just costs one extra scored topic — so when in doubt, include it. Critically, an UNKNOWN or
missing policy area falls back to ALL topics: we never silently skip a bill we can't classify.
A policy area mapped to an empty list is an explicit, reviewed "skip this bill" decision.
"""

from .topics import TopicConfig

# Congress.gov policy area → relevant topic slugs. Empty list = skip the bill.
# Slugs must exist in topics.py; validated by tests.
POLICY_AREA_TOPICS: dict[str, list[str]] = {
    "Health": ["healthcare", "abortion", "social_safety_net", "drug_policy"],
    "Taxation": ["taxation", "government_spending", "national_debt"],
    "Economics and Public Finance": ["government_spending", "national_debt", "taxation"],
    "Armed Forces and National Security": ["military_defense", "foreign_aid"],
    "International Affairs": ["foreign_aid", "military_defense", "trade_policy"],
    "Foreign Trade and International Finance": ["trade_policy", "foreign_aid"],
    "Immigration": ["immigration"],
    "Crime and Law Enforcement": ["criminal_justice", "gun_control", "drug_policy"],
    "Environmental Protection": ["climate_environment"],
    "Energy": ["climate_environment", "government_spending"],
    "Public Lands and Natural Resources": ["climate_environment"],
    "Water Resources Development": ["climate_environment", "government_spending"],
    "Education": ["education"],
    "Labor and Employment": ["labor_unions"],
    "Social Welfare": ["social_safety_net"],
    "Housing and Community Development": ["social_safety_net", "government_spending"],
    "Families": ["social_safety_net", "abortion"],
    "Agriculture and Food": ["social_safety_net", "government_spending"],
    "Emergency Management": ["government_spending"],
    "Science, Technology, Communications": ["tech_privacy"],
    "Civil Rights and Liberties, Minority Issues": ["lgbtq_rights", "voting_elections", "abortion"],
    "Government Operations and Politics": ["government_spending", "voting_elections"],
    "Transportation and Public Works": ["government_spending"],
    # No matching topic in our taxonomy → skip the bill entirely (reviewed decision).
    "Congress": [],
    "Finance and Financial Sector": [],
    "Commerce": [],
    "Law": [],
    "Native Americans": [],
    "Animals": [],
    "Arts, Culture, Religion": [],
    "Sports and Recreation": [],
    "Private Legislation": [],
}


def topics_for_policy_area(
    policy_area: str | None, all_topics: list[TopicConfig]
) -> list[TopicConfig]:
    """The TopicConfig subset to score for a bill in this policy area.

    Returns:
      - the mapped topics (in `all_topics` order) for a known, non-empty area;
      - [] for an area explicitly mapped to no topic → the caller should SKIP the bill;
      - ALL topics for an unknown/missing area → safe fallback, never lose a bill.
    """
    if not policy_area or policy_area not in POLICY_AREA_TOPICS:
        return list(all_topics)
    wanted = set(POLICY_AREA_TOPICS[policy_area])
    return [t for t in all_topics if t.slug in wanted]
