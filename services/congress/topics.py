"""Political topic definitions for bill analysis.

Each topic has a slug (used as cache key), display name, and descriptions
for what -1 and +1 mean on the scoring scale.

Convention: -1 = generally left-leaning position, +1 = generally right-leaning position.
"""

from pydantic import BaseModel


class TopicConfig(BaseModel):
    """A political topic that bills can be scored against."""
    slug: str
    name: str
    minus_one_desc: str
    plus_one_desc: str


# v1.1 topic set: still deliberately narrow. `budget_deficit` REPLACED the older
# `government_spending` axis (and the `national_debt` axis folded into it) — the question we
# actually care about is the gap between what the government spends and what it takes in, not
# the raw spending level. The old slugs' cached scores are deliberately NOT aliased forward:
# they were reasoned about spending levels, so s5 ignores them and the bills get re-scored on
# the new axis (see the top-up path in s4_analysis).
#
# Bills scored on other (now-removed) topics still exist in s4_analysis; s5 simply ignores any
# score whose slug isn't in TOPICS_BY_SLUG, so widening the set later is a one-line change here
# plus a policy-area mapping entry — s4 then tops up only the newly-missing topics.
TOPICS: list[TopicConfig] = [
    TopicConfig(
        slug="military_defense",
        name="Military & Defense Spending",
        minus_one_desc="Less military spending",
        plus_one_desc="More military spending",
    ),
    TopicConfig(
        slug="taxation",
        name="Taxation",
        minus_one_desc="Higher taxes",
        plus_one_desc="Lower taxes",
    ),
    TopicConfig(
        slug="budget_deficit",
        name="Budget Deficit",
        # The axis is deficit tolerance, not spending level: a bill that widens the gap between
        # outlays and revenue scores -1; one that narrows it (cuts, offsets, pay-fors, caps)
        # scores +1. A fully paid-for spending increase is therefore NOT a -1.
        minus_one_desc="Accept a larger deficit to fund priorities",
        plus_one_desc="Shrink the deficit and balance the budget",
    ),
    TopicConfig(
        slug="trade_policy",
        name="Trade Policy",
        # Flipped: protectionism/tariffs is now the left-leaning (−1) pole and free trade
        # the right-leaning (+1) pole, reflecting the current party alignment on trade.
        minus_one_desc="Protectionist policies and tariffs",
        plus_one_desc="Free trade",
    ),
    TopicConfig(
        slug="foreign_aid",
        name="Foreign Aid",
        minus_one_desc="More foreign aid spending",
        plus_one_desc="Less foreign aid spending",
    ),
    TopicConfig(
        slug="healthcare_affordability",
        name="Healthcare Affordability",
        minus_one_desc="Government action to lower costs and expand coverage",
        plus_one_desc="Market competition with less government involvement",
    ),
    TopicConfig(
        slug="money_in_politics",
        name="Money in Politics",
        minus_one_desc="Tighter limits and disclosure for political money",
        plus_one_desc="Fewer restrictions on political spending",
    ),
]

TOPICS_BY_SLUG: dict[str, TopicConfig] = {t.slug: t for t in TOPICS}
