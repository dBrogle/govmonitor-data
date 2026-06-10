"""Classify House roll-call vote questions for alignment scoring.

A roll call is on a specific *question* — final passage, an amendment, or a procedural
motion — not "the bill." The substantive question lives in the `voteQuestion` field of the
house-vote `/members` endpoint header (the list endpoint only carries `voteType`, the voting
*method* like "Yea-and-Nay"). Treating every roll call as a stance on the bill's substance is
wrong: a member's Nay on a procedural Motion to Recommit, or an Aye on an amendment, says
little about their position on the underlying bill.

This module is the single source of truth for how each question type is weighted. It is pure
(no I/O) so it can be unit-tested and reused by any stage.

v0 policy:
  - PASSAGE  → weight 1.0. The substantive up/down vote on the measure itself; its direction
               is the member's stance on the bill we scored.
  - AMENDMENT → weight 0.0 (excluded for now). The vote is on the amendment's text, but we
               currently only score the parent bill, so counting it would misattribute the
               parent's topics to an amendment vote (a Barry-Moore-class error). Phase 2 will
               score the amendment text itself and re-enable these with a real weight.
  - PROCEDURAL → weight 0.0. Previous-question, recommit, table, refer, commit, discharge,
               instruct-conferees, rule consideration, speaker election: party-discipline
               machinery, not a substantive policy position. Excluded, like Present/Not-Voting.
  - OTHER    → weight 1.0 (counts). Unknown/new question text — default to counting at full
               weight rather than silently dropping signal; surfaced via the category so
               unexpected types can be reviewed.
"""

PASSAGE = "passage"
AMENDMENT = "amendment"
PROCEDURAL = "procedural"
OTHER = "other"

# Substring patterns, checked in order. First match wins. Lower-cased comparison.
# Procedural is checked before passage so "...Pass" inside a procedural motion can't be
# misread (none currently collide, but order keeps it safe as questions evolve).
_PROCEDURAL_PATTERNS = (
    "previous question",
    "motion to recommit",
    "motion to table",
    "motion to refer",
    "motion to commit",
    "motion to discharge",
    "instruct conferees",
    "consideration of the resolution",
    "election of the speaker",
    "motion to adjourn",
    "motion to postpone",
    "quorum",
)
_AMENDMENT_PATTERNS = (
    "agreeing to the amendment",
    "agreeing to the senate amendment",   # not "concur" (that's final passage) — see below
    "retaining division",
)
_PASSAGE_PATTERNS = (
    "on passage",
    "suspend the rules and pass",
    "suspend the rules and agree",
    "agreeing to the resolution",
    "agreeing to the conference report",
    "concur in the senate amendment",      # final passage of the Senate-amended measure
    "objections of the president",         # veto override
)

# category → weight applied to both numerator and denominator in alignment scoring.
WEIGHTS = {
    PASSAGE: 1.0,
    AMENDMENT: 0.0,
    PROCEDURAL: 0.0,
    OTHER: 1.0,
}


def classify_vote_question(question: str | None) -> str:
    """Return the category (PASSAGE / AMENDMENT / PROCEDURAL / OTHER) for a vote question."""
    if not question:
        return OTHER
    q = question.lower()
    for p in _PROCEDURAL_PATTERNS:
        if p in q:
            return PROCEDURAL
    for p in _AMENDMENT_PATTERNS:
        if p in q:
            return AMENDMENT
    for p in _PASSAGE_PATTERNS:
        if p in q:
            return PASSAGE
    return OTHER


def vote_weight(question: str | None) -> float:
    """Alignment weight for a vote question (0.0 = does not move the score)."""
    return WEIGHTS[classify_vote_question(question)]
