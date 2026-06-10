"""Stage 4: LLM topic scoring for bills that have XML text.

Runs analysis in parallel using threads (parallelism set by config.parallel_llm_calls).
"""

import json
import os
import time
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


def _analyze_one(
    svc: CongressService,
    congress: int,
    bill_type: str,
    bill_number: str,
    title: str,
    topics: list,
) -> tuple[str, dict | None, str | None]:
    """Analyze a single bill (topic scoring + summary). Returns (tag, result_dict, error_message).

    `topics` is the policy-area-targeted subset to score (not all 19)."""
    tag = f"{bill_type.upper()} {bill_number}"
    try:
        analysis = svc.analyze_bill(congress, bill_type, bill_number, topics=topics)
    except ValueError:
        return tag, None, "no XML available"
    except Exception as e:
        return tag, None, str(e)

    # Generate summary (cached independently)
    try:
        summary = svc.summarize_bill(congress, bill_type, bill_number)
        analysis.summary = summary
    except Exception as e:
        print(f"    {tag}: summary failed ({e})")
        # Non-fatal — analysis still valid without summary

    result = analysis.model_dump()

    out = output_path(congress, bill_type, bill_number)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))

    nonzero = [s for s in analysis.scores if s.score != 0]
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
    voted_keys = _voted_bill_keys() if analyze_voted_only else None

    bill_files = sorted(BILLS_DIR.glob("*.json"))
    bills_with_xml = []
    skipped_unvoted = 0
    skipped_no_topic = 0  # policy-area prefilter: no relevant topic in our taxonomy
    skipped_unreadable = 0  # partial/corrupt file (e.g. mid-write by a concurrent fetch)

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
    if skipped_unreadable:
        print(f"  Skipped {skipped_unreadable} unreadable bill files (likely mid-fetch — re-run later to catch them)")

    # Filter out already-processed bills
    to_process = []
    skipped = 0
    for congress, bill_type, bill_number, title, topics in bills_with_xml:
        out = output_path(congress, bill_type, bill_number)
        if out.exists() and not force:
            skipped += 1
        else:
            to_process.append((congress, bill_type, bill_number, title, topics))

    total = len(to_process)
    print(f"  Found {len(bills_with_xml)} bills with XML, {skipped} already done, {total} to analyze")

    if not to_process:
        return

    # Process in parallel batches
    processed = 0
    errors = 0
    pipeline_start = time.monotonic()
    batch_times: list[float] = []

    num_batches = (total + batch_size - 1) // batch_size

    for batch_idx, batch_start in enumerate(range(0, total, batch_size)):
        batch = to_process[batch_start : batch_start + batch_size]
        batch_num = batch_idx + 1
        done_so_far = batch_start

        # ETA based on average batch time
        if batch_times:
            avg_batch = sum(batch_times) / len(batch_times)
            remaining_batches = num_batches - batch_idx
            eta_secs = avg_batch * remaining_batches
            mins, secs = divmod(int(eta_secs), 60)
            eta_str = f"~{mins}m {secs}s remaining" if mins else f"~{secs}s remaining"
        else:
            eta_str = "estimating..."

        pct = int(done_so_far / total * 100)
        bar_filled = pct // 5
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"\n  [{bar}] {pct}%  batch {batch_num}/{num_batches}  ({done_so_far}/{total} bills)  {eta_str}")

        batch_start_time = time.monotonic()

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(_analyze_one, svc, congress, bt, bn, title, topics): (congress, bt, bn, title)
                for congress, bt, bn, title, topics in batch
            }

            for future in as_completed(futures):
                tag, result, error = future.result()
                if error:
                    print(f"    {tag}: {error}")
                    errors += 1
                else:
                    nonzero = len([s for s in result["scores"] if s["score"] != 0])
                    print(f"    {tag}: done ({nonzero} topics with signal)")
                    processed += 1

        batch_times.append(time.monotonic() - batch_start_time)

    elapsed = time.monotonic() - pipeline_start
    mins, secs = divmod(int(elapsed), 60)
    elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    print(f"\n  Analysis: {processed} processed, {skipped} already existed, {errors} errors ({elapsed_str})")
