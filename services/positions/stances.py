"""LLM scoring of a member's STATED positions from their website text.

Mirrors the bill-scoring convention (−1 = left-leaning pole, +1 = right-leaning pole) so a
member's *stated* stance is directly comparable to their *voted* alignment — that comparison
is the truth score, computed in s5. Requires a verbatim quote as evidence for every stance,
so the frontend can show the receipt and there's nothing to hallucinate.
"""

from pydantic import BaseModel

from services.congress.topics import TOPICS


class StatedStance(BaseModel):
    topic_slug: str
    addressed: bool          # did the text clearly state a position on this topic?
    emphasis: float | None   # 0..1: how much they "preach" this topic (prominence in messaging)
    stated_score: float | None   # −1..+1 on the topic convention, or null if not addressed
    quote: str | None            # exact verbatim supporting quote from the text, or null
    reasoning: str               # one sentence


class StancesResponse(BaseModel):
    stances: list[StatedStance]


SYSTEM_PROMPT = (
    "You analyze a U.S. House member's PUBLIC STATEMENTS — taken verbatim from their official "
    "website, mostly press releases — to determine the member's STATED position on each topic.\n\n"
    "For each topic, score the stated position from -1 to +1 using the SAME convention given in "
    "the prompt (-1 = the topic's minus_one description, +1 = the plus_one description).\n\n"
    "Rules:\n"
    "- Set addressed=true ONLY when the text clearly states a position on that topic. Otherwise "
    "set addressed=false, stated_score=null, quote=null.\n"
    "- When addressed, you MUST include an EXACT VERBATIM quote copied word-for-word from the "
    "provided text as evidence. Never paraphrase or invent a quote. If you cannot find a "
    "verbatim quote that shows the position, treat the topic as NOT addressed.\n"
    "- Set emphasis (0..1) to how CENTRAL this topic is to the member's messaging — how much "
    "they 'preach' it — judged by how prominently and how often it appears across the provided "
    "text. 1.0 = a signature issue they return to repeatedly; ~0.15 = mentioned once in "
    "passing. Set emphasis=null when addressed=false.\n"
    "- Judge ONLY what the text says. Do not infer a position from the member's party, name, "
    "or your own prior knowledge of them.\n"
    "- Keep reasoning to one sentence."
)


def build_user_prompt(member_text: str) -> str:
    lines = "\n".join(
        f"- {t.slug} ({t.name}): -1 = {t.minus_one_desc}; +1 = {t.plus_one_desc}"
        for t in TOPICS
    )
    return f"TOPICS (scoring convention):\n{lines}\n\nMEMBER PUBLIC-STATEMENT TEXT:\n{member_text}"


def score_stances(llm, member_text: str) -> list[StatedStance]:
    """Return the member's stated stance per topic (structured, verbatim-quoted)."""
    resp: StancesResponse = llm.structured_completion(
        SYSTEM_PROMPT, build_user_prompt(member_text), StancesResponse
    )
    return resp.stances
