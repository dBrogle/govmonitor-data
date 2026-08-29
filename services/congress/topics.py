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


# v1 topic set: deliberately narrow. Government Spending absorbs national debt (see
# TOPIC_ALIASES in pipeline/stages/s5_alignment.py) — the two are treated as one axis.
# Bills scored on other (now-removed) topics still exist in s4_analysis; s5 simply ignores
# any score whose slug isn't in TOPICS_BY_SLUG, so widening the set later is a one-line change.
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
        slug="government_spending",
        name="Government Spending",
        minus_one_desc="More government spending",
        plus_one_desc="Less government spending",
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
]

TOPICS_BY_SLUG: dict[str, TopicConfig] = {t.slug: t for t in TOPICS}
