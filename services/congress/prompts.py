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

You will be given the full XML text of a bill and a list of political topics to evaluate.

Scoring rules:
- Use a scale from -1.0 to 1.0 with increments as fine as 0.1.
- 0 means the bill has NO bearing on the topic whatsoever.
- Small magnitudes (±0.1 to ±0.3) mean the bill is tangentially related or has minor implications.
- Medium magnitudes (±0.4 to ±0.6) mean the bill has moderate, clear impact on the topic.
- Large magnitudes (±0.7 to ±1.0) mean the bill is primarily about or very strongly affects the topic.
- Negative scores mean the bill pushes toward the -1 end of the spectrum (described per topic).
- Positive scores mean the bill pushes toward the +1 end of the spectrum (described per topic).

Be precise. Most bills will score 0 on most topics. Only assign a nonzero score when the bill \
text genuinely engages with the topic.

For each topic, you MUST provide "thoughts" before "score". In your thoughts, reason step-by-step \
about whether/how the bill relates to that specific topic before deciding on a score. \
Do not decide the score first and then rationalize it — let the score follow from your reasoning.

Return one entry per topic in the same order they are listed, using each topic's slug as the \
topic_slug field."""


def build_topics_user_prompt(bill_xml: str, topics: list[TopicConfig]) -> str:
    """Build the user prompt for analyzing a bill against all topics at once."""
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

<bill_xml>
{bill_xml}
</bill_xml>"""
