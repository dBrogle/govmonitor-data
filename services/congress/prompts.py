"""Prompt templates for LLM-based bill analysis."""

from .topics import TopicConfig


BILL_SUMMARY_SYSTEM_PROMPT = """\
You are an expert, nonpartisan legislative analyst. You write extremely concise \
plain-language summaries of U.S. legislation for a general audience.

Given the full XML text of a bill, produce exactly 1 sentence that captures \
the bill's primary purpose and key mechanism. Avoid jargon. Be specific about what \
the bill does, not just its stated goals."""


def build_summary_user_prompt(bill_xml: str) -> str:
    """Build the user prompt for summarizing a bill."""
    return f"""\
Summarize the following bill in exactly 1 concise sentence.

<bill_xml>
{bill_xml}
</bill_xml>"""


BILL_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert, nonpartisan legislative analyst. You evaluate how strongly \
a piece of U.S. legislation affects a set of political topics.

You will be given the text of a bill — either its full text or its official (CRS) summary — \
and a list of political topics to evaluate.

Scoring rules:
- Use a scale from -1.0 to 1.0 with increments as fine as 0.1.
- 0 means the bill has NO bearing on the topic whatsoever.
- Small magnitudes (±0.1 to ±0.3) mean the bill is tangentially related or has minor implications.
- Medium magnitudes (±0.4 to ±0.6) mean the bill has moderate, clear impact on the topic.
- Large magnitudes (±0.7 to ±1.0) mean the bill is primarily about or very strongly affects the topic.
- Negative scores mean the bill pushes toward the -1 end of the spectrum (described per topic).
- Positive scores mean the bill pushes toward the +1 end of the spectrum (described per topic).

CENTRALITY IS DECISIVE. Score how central the topic is to the bill *as a whole* — not merely \
whether some provision touches it. The magnitude must reflect how much the bill is actually \
ABOUT the topic and how much a vote on the entire bill is effectively a vote on that topic. \
A single clause buried in a large bill devoted mostly to something else (e.g. one social \
provision inside a sprawling defense or appropriations bill) is tangential to the bill: score \
it ±0.1 to ±0.3 AT MOST, even if that clause, read in isolation, would be a strong statement. \
Reserve ±0.7 to ±1.0 for bills whose primary purpose IS the topic. Ask yourself: "What share \
of this bill is about this topic, and would a reasonable person call this a '[topic]' bill?" \
If only a small share, the magnitude must be small regardless of how pointed that share is.

Be precise. Most bills will score 0 on most topics. Only assign a nonzero score when the bill \
text genuinely engages with the topic, and let the magnitude track the topic's centrality to \
the bill overall.

Consider EVERY topic listed and weigh whether the bill affects it. But in your response, \
return an entry ONLY for topics that score nonzero — omit every topic that scores 0 (an \
omitted topic is treated as exactly 0). Most topics will be omitted for most bills; that is \
expected and correct.

For each topic you include, provide "thoughts" before "score": one or two concise sentences \
explaining how the bill engages the topic and how central it is to the bill, then the score. \
Let the score follow from the reasoning, not the reverse. Use each topic's slug as the \
topic_slug field."""


def build_topics_user_prompt(bill_text: str, topics: list[TopicConfig]) -> str:
    """Build the user prompt for analyzing a bill against all topics at once.

    `bill_text` may be the full bill text or its official summary (see analyze_bill)."""
    topic_lines = []
    for t in topics:
        topic_lines.append(
            f"- **{t.name}** (slug: `{t.slug}`)\n"
            f"  -1 = {t.minus_one_desc}\n"
            f"  +1 = {t.plus_one_desc}"
        )
    topics_block = "\n".join(topic_lines)

    return f"""\
Analyze the following bill against each of these political topics. For each topic, think \
step-by-step about how (if at all) the bill relates to it, then assign a score.

**Topics:**
{topics_block}

<bill_text>
{bill_text}
</bill_text>"""
