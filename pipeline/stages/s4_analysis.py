"""Stage 4: LLM topic scoring for bills that have XML text.

Runs analysis in parallel using threads (parallelism set by config.parallel_llm_calls).

INCREMENTAL BY TOPIC. A bill is not "done" just because an output file exists — it's done when
that file holds a score for every topic the bill's policy area targets. Adding a topic to
topics.py therefore costs one LLM call per *relevant* bill scoring *only the missing topics*,
merged into the existing file, instead of a full re-score of the whole corpus (~4x cheaper on
the 119th). `--force` still re-scores every targeted topic from scratch.

Because a file can then hold scores from several runs, provenance is stamped PER SCORE
(`llm_model` / `temperature` / `scored_at`), not just per file — the file-level stamp records
the most recent run only.
"""

import json
import os
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from services.congress.congress import CongressService
from services.congress.policy_areas import topics_for_policy_area
from services.congress.topics import TOPICS
from services.llm.openrouter import OpenRouterService

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s4_analysis"
BILLS_DIR = Path(__file__).parent.parent / "output" / "s3_bills"
VOTES_DIR = Path(__file__).parent.parent / "output" / "s3_votes"

DEFAULT_PARALLEL_LLM_CALLS = 20

# Fallback pricing per 1M tokens (input, output) when OpenRouter doesn't report a cost.
# Verify against live OpenRouter rates — model prices change.
PRICING = {"x-ai/grok-4.3": (3.0, 15.0)}


def _spend(llm) -> float:
    """Best-available run cost: OpenRouter's own figure if present, else a token estimate."""
    if getattr(llm, "cost", 0.0):
        return llm.cost
    pin, pout = PRICING.get(getattr(llm, "model", ""), (3.0, 15.0))
    return llm.prompt_tokens / 1e6 * pin + llm.completion_tokens / 1e6 * pout


def output_path(congress: int, bill_type: str, bill_number: str) -> Path:
    return OUTPUT_DIR / f"{congress}_{bill_type}_{bill_number}.json"


def _voted_bill_keys() -> set[tuple[int, str, str]]:
    """(congress, type, number) for every bill that received a recorded floor vote.

    v0 only scores bills that reached a vote — that's where a member's actual impact
    shows. Bills merely sponsored/cosponsored but never voted are intentionally left
    unanalyzed for now (see data/notes.txt; revisit in v2 with sponsor-weighting)."""
    keys: set[tuple[int, str, str]] = set()
    if not VOTES_DIR.exists():
        return keys
    for vf in VOTES_DIR.glob("*.json"):
        data = json.loads(vf.read_text())
        b = data.get("bill") or {}
        if b.get("type") and b.get("number"):
            keys.add((data["congress"], b["type"].upper(), str(b["number"])))
    return keys


def _existing_scores(path: Path) -> tuple[dict, dict[str, dict]]:
    """The prior analysis file and its scores by slug ({}, {} when there is none).

    Old scores are kept even for topics no longer in topics.py — s5 ignores unknown slugs, and
    keeping them means a resurrected topic costs nothing to bring back."""
    if not path.exists():
        return {}, {}
    try:
        prior = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}, {}
    return prior, {sc["topic_slug"]: sc for sc in prior.get("scores", [])}


def missing_topics(path: Path, targeted: list, *, force: bool) -> list:
    """The targeted topics this bill has no score for yet (all of them under --force)."""
    if force:
        return list(targeted)
    _, by_slug = _existing_scores(path)
    return [t for t in targeted if t.slug not in by_slug]


def _analyze_one(
    svc: CongressService,
    congress: int,
    bill_type: str,
    bill_number: str,
    title: str,
    topics: list,
) -> tuple[str, dict | None, str | None]:
    """Score a single bill on `topics` and MERGE the result into any existing output file.

    `topics` is the missing subset for this bill (see missing_topics) — not all of them, and
    not all 19. Returns (tag, merged_result, error_message)."""
    tag = f"{bill_type.upper()} {bill_number}"
    try:
        analysis = svc.analyze_bill(congress, bill_type, bill_number, topics=topics)
    except ValueError:
        return tag, None, "no XML available"
    except Exception as e:
        return tag, None, str(e)

    out = output_path(congress, bill_type, bill_number)
    prior, prior_by_slug = _existing_scores(out)

    # Generate summary (cached independently). Reuse a prior one rather than paying again.
    if prior.get("summary"):
        analysis.summary = prior["summary"]
    else:
        try:
            analysis.summary = svc.summarize_bill(congress, bill_type, bill_number)
        except Exception as e:
            print(f"    {tag}: summary failed ({e})")
            # Non-fatal — analysis still valid without summary

    result = analysis.model_dump()

    # The scoring prompt asks the model to OMIT any topic it scores 0 ("no bearing on the
    # bill"), so a requested topic can legitimately come back absent. Persist those as explicit
    # 0.0 entries: without them "absent" would mean both "never asked" and "asked, scored zero",
    # so missing_topics would queue the bill again on every future run and re-pay for the same
    # answer. Recording the zero is faithful — the prompt defines an omission as exactly 0.
    returned = {sc["topic_slug"] for sc in result["scores"]}
    for t in topics:
        if t.slug not in returned:
            result["scores"].append({
                "topic_slug": t.slug,
                "topic_name": t.name,
                "score": 0.0,
                "thoughts": "Scored but omitted by the model, which the prompt defines as no "
                            "bearing on this topic.",
            })

    # Stamp provenance so every scored file records exactly what produced it. Without this,
    # recovering which model/temperature made the dataset takes log-and-git forensics. Scores
    # can come from different runs, so the stamp lives on each score; the file-level fields
    # describe the most recent run.
    llm = getattr(svc, "llm_service", None)
    model, temperature = getattr(llm, "model", None), getattr(llm, "temperature", None)
    scored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sc in result["scores"]:
        sc["llm_model"], sc["temperature"], sc["scored_at"] = model, temperature, scored_at

    # Merge: this run's scores win for the topics it covered; every other prior score is kept,
    # backfilled with the file-level stamp it was originally written under.
    merged = {
        slug: {
            **sc,
            "llm_model": sc.get("llm_model", prior.get("llm_model")),
            "temperature": sc.get("temperature", prior.get("temperature")),
        }
        for slug, sc in prior_by_slug.items()
    }
    merged.update({sc["topic_slug"]: sc for sc in result["scores"]})
    result["scores"] = list(merged.values())
    result["llm_model"] = model
    result["temperature"] = temperature

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))

    return tag, result, None


def run(candidates: list[dict], config: dict, *, force: bool = False):
    openrouter_key = config.get("_openrouter_api_key")
    if not openrouter_key or openrouter_key == "put_key_here":
        print("  [!] OPENROUTER_API_KEY not set — cannot run LLM analysis")
        return

    batch_size = config.get("parallel_llm_calls", DEFAULT_PARALLEL_LLM_CALLS)
    # Model is configurable (OPENROUTER_MODEL env or config "llm_model") so a provider
    # deprecation is a one-line change, not a code edit. OpenRouter retires model slugs
    # over time (a stale slug returns 404), so pin to a current, structured-output-capable one.
    model = os.getenv("OPENROUTER_MODEL") or config.get("llm_model")
    llm = OpenRouterService(api_key=openrouter_key, model=model) if model else OpenRouterService(api_key=openrouter_key)
    print(f"  Using LLM model: {llm.model}")
    svc = CongressService(api_key=config["_congress_api_key"], llm_service=llm)

    # Find all bills with XML text from the bills stage output
    if not BILLS_DIR.exists():
        print("  [!] No bills output found — run the 'bills' stage first")
        return

    # v0: only analyze bills that reached a floor vote — that's the impact signal that
    # feeds alignment. Sponsored/cosponsored-but-unvoted bills are left unanalyzed for
    # now (config "analyze_voted_only"; see data/notes.txt for the v2 plan).
    analyze_voted_only = config.get("analyze_voted_only", True)
    # Top-up mode: consider ONLY bills that already have an analysis file. Used when a topic is
    # added and the goal is to bring the SHIPPED dataset up to the new topic set, without also
    # pulling in bills that have never been scored at all (a much larger, separate spend).
    # It supersedes analyze_voted_only, since the existing corpus spans more than voted bills.
    topup_only = config.get("topup_only", False)
    if topup_only:
        analyze_voted_only = False
        print("  Top-up mode: only bills with an existing analysis file will be considered")
    voted_keys = _voted_bill_keys() if analyze_voted_only else None

    bill_files = sorted(BILLS_DIR.glob("*.json"))
    bills_with_xml = []
    skipped_unvoted = 0
    skipped_no_topic = 0  # policy-area prefilter: no relevant topic in our taxonomy
    skipped_unreadable = 0  # partial/corrupt file (e.g. mid-write by a concurrent fetch)
    skipped_unanalyzed = 0  # top-up mode: never scored before, so out of scope for this run

    for bill_file in bill_files:
        # A concurrent bill fetch may be mid-write on this file; a valid s3_bills JSON means
        # the bill is fully fetched (it's written last). Skip unreadable ones rather than
        # crashing the whole batch — they'll be picked up on a later run.
        try:
            data = json.loads(bill_file.read_text())
        except (ValueError, OSError):
            skipped_unreadable += 1
            continue
        if not data.get("has_xml"):
            continue
        key = (data["congress"], data["bill_type"].upper(), str(data["bill_number"]))
        if voted_keys is not None and key not in voted_keys:
            skipped_unvoted += 1
            continue
        if topup_only and not output_path(*key).exists():
            skipped_unanalyzed += 1
            continue
        # Policy-area targeting: score only the relevant topics, and skip bills whose policy
        # area maps to none of our topics (post-office namings, internal congressional
        # matters, etc.). Unknown/missing areas fall back to all topics — never lose a bill.
        topics = topics_for_policy_area(data["detail"].get("policy_area"), TOPICS)
        if not topics:
            skipped_no_topic += 1
            continue
        bills_with_xml.append((
            data["congress"],
            data["bill_type"].upper(),
            data["bill_number"],
            data["detail"].get("title", "(untitled)"),
            topics,
        ))

    if analyze_voted_only:
        print(f"  Voted-only mode: {skipped_unvoted} unvoted bills skipped (v0; sponsored/cosponsored analysis is v2)")
    print(f"  Policy-area prefilter: {skipped_no_topic} bills skipped (no relevant topic)")
    if skipped_unanalyzed:
        print(f"  Top-up mode: {skipped_unanalyzed} never-analyzed bills skipped (out of scope)")
    if skipped_unreadable:
        print(f"  Skipped {skipped_unreadable} unreadable bill files (likely mid-fetch — re-run later to catch them)")

    # Keep only bills that are MISSING at least one targeted topic — a bill already scored on
    # every topic its policy area targets is done, and a bill scored on some of them is
    # topped up with just the rest (one call, only the new topics in the output tokens).
    to_process = []
    skipped = 0
    partially_scored = 0  # existing file already covering SOME of the targeted topics
    for congress, bill_type, bill_number, title, topics in bills_with_xml:
        out = output_path(congress, bill_type, bill_number)
        todo = missing_topics(out, topics, force=force)
        if not todo:
            skipped += 1
            continue
        if out.exists() and len(todo) < len(topics):
            partially_scored += 1
        to_process.append((congress, bill_type, bill_number, title, todo))

    total = len(to_process)
    print(f"  Found {len(bills_with_xml)} bills with XML, {skipped} fully scored, {total} to analyze "
          f"({partially_scored} of them already hold some of their targeted topics)")

    if not to_process:
        return

    # One continuous worker pool over every bill, NOT sequential batches. Batching made each
    # group wait for its slowest member, and bill texts vary from a page to an omnibus — so a
    # single oversized bill idled every other worker until it returned (measured: throughput
    # collapsed from ~57 bills/min to ~3 as the big bills came up). With a shared queue a slow
    # call costs only its own worker.
    processed = 0
    errors = 0
    pipeline_start = time.monotonic()

    # Progress is reported every PROGRESS_EVERY completions rather than per batch.
    PROGRESS_EVERY = max(1, min(40, total // 20 or 1))

    with ThreadPoolExecutor(max_workers=batch_size) as executor:
        futures = [
            executor.submit(_analyze_one, svc, congress, bt, bn, title, topics)
            for congress, bt, bn, title, topics in to_process
        ]

        for future in as_completed(futures):
            tag, result, error = future.result()
            if error:
                print(f"    {tag}: {error}")
                errors += 1
            else:
                nonzero = len([s for s in result["scores"] if s["score"] != 0])
                print(f"    {tag}: done ({nonzero} topics with signal)")
                processed += 1

            done_so_far = processed + errors
            if done_so_far % PROGRESS_EVERY == 0 or done_so_far == total:
                # ETA from the observed completion rate so far — steadier than a batch average
                # once the pool is saturated.
                rate = done_so_far / max(time.monotonic() - pipeline_start, 1e-6)
                eta_secs = int((total - done_so_far) / rate) if rate else 0
                mins, secs = divmod(eta_secs, 60)
                eta_str = f"~{mins}m {secs}s remaining" if mins else f"~{secs}s remaining"

                pct = int(done_so_far / total * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                # Running spend as it climbs: topic top-ups are billed per bill-text prompt, so
                # a wide topic addition can get expensive quietly.
                print(f"\n  [{bar}] {pct}%  ({done_so_far}/{total} bills)  {eta_str}"
                      f"  · running ~${_spend(llm):.2f}\n")

    elapsed = time.monotonic() - pipeline_start
    mins, secs = divmod(int(elapsed), 60)
    elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    src = "OpenRouter-reported" if getattr(llm, "cost", 0.0) else "estimated"
    print(f"\n  Analysis: {processed} processed, {skipped} already existed, {errors} errors ({elapsed_str})")
    print(f"  Spend: {llm.prompt_tokens:,} in + {llm.completion_tokens:,} out tokens "
          f"· ~${_spend(llm):.2f} total ({src})")
