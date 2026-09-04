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
    # ── Areas we score ────────────────────────────────────────────────────────────────────
    # `budget_deficit` is mapped broadly on purpose: nearly any bill that moves outlays or
    # revenue moves the deficit, and it replaced the old `government_spending` axis, so it
    # inherits that axis's (deliberately generous) footprint.
    "Health": ["healthcare_affordability", "budget_deficit"],
    "Social Welfare": ["healthcare_affordability", "budget_deficit"],
    "Taxation": ["taxation", "budget_deficit"],
    "Economics and Public Finance": ["budget_deficit", "taxation"],
    "Armed Forces and National Security": ["military_defense", "foreign_aid"],
    "International Affairs": ["foreign_aid", "military_defense", "trade_policy"],
    "Foreign Trade and International Finance": ["trade_policy", "foreign_aid"],
    "Energy": ["budget_deficit"],
    "Public Lands and Natural Resources": ["budget_deficit"],
    "Water Resources Development": ["budget_deficit"],
    "Housing and Community Development": ["budget_deficit"],
    "Agriculture and Food": ["budget_deficit"],
    "Emergency Management": ["budget_deficit"],
    "Transportation and Public Works": ["budget_deficit"],
    "Government Operations and Politics": ["budget_deficit", "money_in_politics"],
    # Campaign finance, lobbying, ethics and member stock-trading bills live under "Congress"
    # — the flagship money-in-politics legislation. The area used to be skipped outright
    # because no live topic covered it.
    "Congress": ["money_in_politics"],
    "Civil Rights and Liberties, Minority Issues": ["money_in_politics"],

    # ── No matching topic in the current taxonomy → skip the bill entirely ─────────────────
    # Reviewed decisions, not oversights. Several of these (immigration, crime, education,
    # environment, labor) were live topics in the wider pre-v1 taxonomy; if a topic returns,
    # re-point its area here and s4's top-up will score the backlog on just that topic.
    "Immigration": [],
    "Crime and Law Enforcement": [],
    "Environmental Protection": [],
    "Education": [],
    "Labor and Employment": [],
    "Families": [],
    "Science, Technology, Communications": [],
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
