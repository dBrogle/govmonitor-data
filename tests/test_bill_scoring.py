"""Verify the bill-scoring prompt produces *reasonable* topic scores on real bills.

These are the guardrail tests for the analysis prompt (see ALIGNMENT_QUALITY_PLAN.md). They
run the real scoring model on a curated set of bills and assert each score falls in a sane
band. Bands check sign + rough magnitude, not exact values — the LLM is non-deterministic
even at temperature 0, so a point-equality assertion would be flaky. We pick bands wide
enough to be stable but tight enough to catch the failure modes we care about.

The headline case is the NDAA (HR 3838): a sprawling defense bill containing one clause that
restricts gender-transition care. The old prompt scored that buried clause +1.0 on the
then-tracked lgbtq_rights topic (a single provision dominating the whole bill), which —
combined with a vote-handling bug — made a Republican read as "100% pro-LGBT." That topic is
no longer tracked, so the centrality guard now rides on `healthcare_affordability`: the same
clause is the only healthcare content in a defense bill, and must stay near zero.
`_all_abs_max` cases assert a bill is essentially null across every topic (a bill off all our
axes must not hallucinate signal).

Topic poles (from topics.py), so the expected signs are unambiguous:
  military_defense:         -1 less military spending  ·  +1 more military spending
  taxation:                 -1 higher taxes            ·  +1 lower taxes
  budget_deficit:           -1 accept a larger deficit ·  +1 shrink the deficit
  healthcare_affordability: -1 government action       ·  +1 market competition
  money_in_politics:        -1 tighter limits          ·  +1 fewer restrictions

Run:  cd data && pytest tests/test_bill_scoring.py -v
(Costs a few cents on gemini-2.5-flash; skipped automatically without OPENROUTER_API_KEY.)
"""

import pytest

CONGRESS = 119

# Each case: (bill_type, bill_number, expected). `expected` maps topic_slug -> (min, max)
# inclusive band. A special "_all_abs_max" key asserts EVERY topic's |score| <= that value
# (for bills that should be politically null).
CASES = [
    pytest.param(
        "hr", "3838",
        {
            "military_defense": (0.6, 1.0),       # primary purpose: authorize defense
            "budget_deficit": (-1.0, -0.3),       # authorizes hundreds of billions, unoffset
            "healthcare_affordability": (-0.3, 0.3),  # one buried clause must NOT dominate
        },
        id="ndaa-defense-bill",
    ),
    pytest.param(
        "hr", "140",
        {
            "taxation": (0.3, 1.0),               # disaster tax relief = lower taxes
            "money_in_politics": (-0.2, 0.2),     # unrelated
            "military_defense": (-0.2, 0.2),      # unrelated
        },
        id="hurricane-tax-relief",
    ),
    pytest.param(
        # A substantive bill that sits off every axis we track (firearms records privacy —
        # its policy area maps to no topic, so production skips it outright). A more demanding
        # null case than a naming bill: the model must decline to force it onto a live topic.
        "hr", "7678",
        {
            "_all_abs_max": 0.3,
        },
        id="off-axis-gun-owner-privacy-null",
    ),
    pytest.param(
        "hr", "1431",
        {
            "_all_abs_max": 0.15,                 # post-office naming: null on every topic
        },
        id="post-office-naming-null",
    ),
    pytest.param(
        # Regression guard: this budget resolution (which enables the GOP tax-cut
        # reconciliation) read as a tax *increase* under summary-first because the CRS summary
        # is neutral process-language. Full-text-first must score it as a tax cut (positive).
        "hconres", "14",
        {
            "taxation": (0.3, 1.0),               # enables tax cuts = lower taxes
            # Deliberately unsigned: the resolution both enables large tax cuts (widening the
            # deficit) and instructs committees to find spending cuts (narrowing it), so a
            # defensible score exists on either side. What must NOT happen is a 0 — a budget
            # resolution is the most deficit-central document Congress produces, and a zero
            # would mean the axis was lost entirely.
            "_nonzero": ["budget_deficit"],
        },
        id="budget-resolution-fulltext",
    ),
]


@pytest.mark.llm
@pytest.mark.parametrize("bill_type, bill_number, expected", CASES)
def test_bill_topic_scores_are_reasonable(scoring_service, bill_type, bill_number, expected):
    analysis = scoring_service.analyze_bill(CONGRESS, bill_type, bill_number, force=True)
    # Lean output: only nonzero topics are returned, so a topic absent from the response
    # means a score of exactly 0.
    scores = {s.topic_slug: s.score for s in analysis.scores}

    nonzero = {k: round(v, 2) for k, v in scores.items() if v != 0}
    ctx = f"[{bill_type.upper()} {bill_number} via {analysis.text_source}] nonzero={nonzero}"

    all_abs_max = expected.pop("_all_abs_max", None)
    # Topics that must carry SOME signal, where the defensible sign is genuinely contested.
    for slug in expected.pop("_nonzero", []):
        assert abs(scores.get(slug, 0.0)) > 0, (
            f"{ctx}: {slug}=0 — the bill is centrally about this axis, so a zero means the "
            "topic was dropped, not that the bill is neutral on it"
        )
    if all_abs_max is not None:
        worst = max(scores.items(), key=lambda kv: abs(kv[1]), default=(None, 0))
        assert abs(worst[1]) <= all_abs_max, (
            f"{ctx}: expected all |score| <= {all_abs_max}, but {worst[0]}={worst[1]:.2f}"
        )

    for slug, (lo, hi) in expected.items():
        score = scores.get(slug, 0.0)  # omitted topic == 0
        assert lo <= score <= hi, (
            f"{ctx}: {slug}={score:.2f} outside expected [{lo}, {hi}]"
        )
