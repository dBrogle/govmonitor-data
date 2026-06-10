"""Verify the bill-scoring prompt produces *reasonable* topic scores on real bills.

These are the guardrail tests for the analysis prompt (see ALIGNMENT_QUALITY_PLAN.md). They
run the real scoring model on a curated set of bills and assert each score falls in a sane
band. Bands check sign + rough magnitude, not exact values — the LLM is non-deterministic
even at temperature 0, so a point-equality assertion would be flaky. We pick bands wide
enough to be stable but tight enough to catch the failure modes we care about.

The headline case is the NDAA (HR 3838): a sprawling defense bill containing one clause that
restricts gender-transition care. The old prompt scored it lgbtq_rights = +1.0 (a single
buried provision dominating the whole bill), which — combined with a vote-handling bug — made
a Republican read as "100% pro-LGBT." The centrality rule in the prompt (plus summary-first
input) should now keep that score small. `_all_abs_max` cases assert a bill is essentially
null across every topic (commemorative/naming bills must not hallucinate signal).

Topic poles (from topics.py), so the expected signs are unambiguous:
  military_defense:    -1 less military spending  ·  +1 more military spending
  government_spending: -1 more spending           ·  +1 less spending
  taxation:            -1 higher taxes             ·  +1 lower taxes
  gun_control:         -1 stricter gun rules       ·  +1 protect gun rights
  lgbtq_rights:        -1 pro LGBT+ protections    ·  +1 limit LGBT+ protections

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
            "government_spending": (-1.0, -0.4),  # authorizes hundreds of billions -> more spending
            "lgbtq_rights": (-0.3, 0.35),         # one buried clause must NOT dominate (was +1.0)
        },
        id="ndaa-defense-bill",
    ),
    pytest.param(
        "hr", "140",
        {
            "taxation": (0.3, 1.0),               # disaster tax relief = lower taxes
            "lgbtq_rights": (-0.2, 0.2),          # unrelated
            "immigration": (-0.2, 0.2),           # unrelated
        },
        id="hurricane-tax-relief",
    ),
    pytest.param(
        "hr", "7678",
        {
            "gun_control": (0.2, 1.0),            # shields gun-owner records = protect gun rights
        },
        id="gun-owner-privacy",
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
            "national_debt": (0.3, 1.0),          # raises debt limit / debt-reduction framing
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
