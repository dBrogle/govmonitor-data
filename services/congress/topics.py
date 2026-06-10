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


TOPICS: list[TopicConfig] = [
    TopicConfig(
        slug="government_spending",
        name="Government Spending",
        minus_one_desc="More government spending",
        plus_one_desc="Less government spending",
    ),
    TopicConfig(
        slug="taxation",
        name="Taxation",
        minus_one_desc="Higher taxes",
        plus_one_desc="Lower taxes",
    ),
    TopicConfig(
        slug="healthcare",
        name="Healthcare",
        minus_one_desc="More public healthcare",
        plus_one_desc="Private healthcare",
    ),
    TopicConfig(
        slug="gun_control",
        name="Gun Control",
        minus_one_desc="Stricter gun rules",
        plus_one_desc="Protect gun rights",
    ),
    TopicConfig(
        slug="immigration",
        name="Immigration",
        minus_one_desc="More immigration",
        plus_one_desc="Secure Borders",
    ),
    TopicConfig(
        slug="abortion",
        name="Abortion",
        minus_one_desc="Pro-choice",
        plus_one_desc="Pro-life",
    ),
    TopicConfig(
        slug="military_defense",
        name="Military & Defense Spending",
        minus_one_desc="Less military spending",
        plus_one_desc="More military spending",
    ),
    TopicConfig(
        slug="climate_environment",
        name="Climate & Environment",
        minus_one_desc="More environmental protection",
        plus_one_desc="Less environmental regulations",
    ),
    TopicConfig(
        slug="social_safety_net",
        name="Social Safety Net",
        minus_one_desc="Expand welfare",
        plus_one_desc="Reduce welfare spending",
    ),
    TopicConfig(
        slug="education",
        name="Education",
        minus_one_desc="More spending on public schools",
        plus_one_desc="Less spending on public schools",
    ),
    TopicConfig(
        slug="drug_policy",
        name="Drug Policy",
        minus_one_desc="Relaxed drug enforcement",
        plus_one_desc="Stricter drug enforcement",
    ),
    TopicConfig(
        slug="criminal_justice",
        name="Criminal Justice",
        minus_one_desc="Softer justice, more rehabilitation",
        plus_one_desc="Stricter justice and stronger policing",
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
        slug="national_debt",
        name="National Debt",
        minus_one_desc="Accept deficit spending for investment",
        plus_one_desc="Prioritize debt reduction",
    ),
    TopicConfig(
        slug="lgbtq_rights",
        name="LGBTQ+ Rights",
        minus_one_desc="Pro LGBT+ Legal Protections",
        plus_one_desc="Limit LGBT+ Legal Protections",
    ),
    TopicConfig(
        slug="foreign_aid",
        name="Foreign Aid",
        minus_one_desc="More foreign aid spending",
        plus_one_desc="Less foreign aid spending",
    ),
    TopicConfig(
        slug="voting_elections",
        name="Voting & Elections",
        minus_one_desc="Expand voting access and election reform",
        plus_one_desc="Enforce voter ID and secure elections",
    ),
    TopicConfig(
        slug="tech_privacy",
        name="Tech & Privacy",
        minus_one_desc="More oversight and data protection",
        plus_one_desc="Less regulation on tech and privacy",
    ),
    TopicConfig(
        slug="labor_unions",
        name="Labor & Unions",
        minus_one_desc="More labor laws and union rights",
        plus_one_desc="Employer flexibility and limit union power",
    ),
]

TOPICS_BY_SLUG: dict[str, TopicConfig] = {t.slug: t for t in TOPICS}
