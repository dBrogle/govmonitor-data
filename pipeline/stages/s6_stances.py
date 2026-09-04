"""Stage 6: extract each member's STATED positions from their official website.

Complements the vote-based alignment (s5). For each member we scrape press-release / issues
text from their house.gov site and have the LLM score their stated stance — with a verbatim
quote — on each topic, using the same −1..+1 convention as bill scoring. s5 then pairs stated
vs voted per topic and computes the truth score.

This stage hits the open web and should be re-run periodically, since members' public
statements change over time (`--force` to refresh). It reads each member's website from the
s1 members output, so it must run after `members`.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from services.congress.topics import TOPICS
from services.llm.openrouter import OpenRouterService
from services.positions.scraper import gather_member_text
from services.positions.stances import score_stances

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s6_stances"
# The scraped text is CACHED to disk, separately from the scores derived from it. Crawling 431
# member sites is the slow, fragile, rate-limited half of this stage; scoring is the cheap half.
# Caching the corpus means ADDING A TOPIC costs one LLM call per member and NO crawling at all.
CORPUS_DIR = Path(__file__).parent.parent / "output" / "s6_corpus"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"

# Scraping and scoring have completely different rate limits, so they get separate pools.
# house.gov member sites sit behind shared front-end infrastructure that starts refusing
# connections somewhere above ~12 concurrent (measured: 40 workers → 391/431 sites unreachable),
# while a single member's whole crawl takes only ~3s. The LLM call on a 22K-char corpus is the
# actual bottleneck, and OpenRouter is happy with far more concurrency than house.gov is.
# One knob for both meant the safe scrape limit throttled scoring to a crawl.
DEFAULT_SCRAPE_PARALLEL = 8
DEFAULT_SCORE_PARALLEL = 30

# How old a cached corpus may be before it's re-crawled. Members' public statements change, so
# the text does go stale — but only re-crawl on purpose, never as a side effect of adding a
# topic. `--refresh-corpus` forces a re-crawl regardless of age.
DEFAULT_CORPUS_MAX_AGE_DAYS = 30

# Fallback pricing per 1M tokens (input, output) when OpenRouter doesn't report a cost.
# Verify against live OpenRouter rates — model prices change.
PRICING = {"x-ai/grok-4.3": (3.0, 15.0)}


def _spend(llm: OpenRouterService) -> float:
    """Best-available run cost: OpenRouter's own figure if present, else a token estimate."""
    if getattr(llm, "cost", 0.0):
        return llm.cost
    pin, pout = PRICING.get(llm.model, (3.0, 15.0))
    return llm.prompt_tokens / 1e6 * pin + llm.completion_tokens / 1e6 * pout


def output_path(state: str, district: int) -> Path:
    return OUTPUT_DIR / f"{state}_{district}.json"


def corpus_path(state: str, district: int) -> Path:
    return CORPUS_DIR / f"{state}_{district}.json"


def _read_corpus(state: str, district: int, max_age_days: int) -> dict | None:
    """The cached scrape for this member, if present and still fresh enough to reuse."""
    f = corpus_path(state, district)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except (ValueError, OSError):
        return None
    fetched = data.get("fetched_at")
    if not fetched:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched)
    except ValueError:
        return None
    return data if age.days < max_age_days else None


def missing_topics_for(state: str, district: int, *, force: bool) -> list:
    """Topics this member has no stated-stance entry for yet (all of them under --force).

    Mirrors s4's incremental scoring: a member is "done" when their file covers every current
    topic, so growing the topic set re-scores only what's genuinely new."""
    if force:
        return list(TOPICS)
    f = output_path(state, district)
    if not f.exists():
        return list(TOPICS)
    try:
        have = {s["topic_slug"] for s in json.loads(f.read_text()).get("stances", [])}
    except (ValueError, OSError):
        return list(TOPICS)
    return [t for t in TOPICS if t.slug not in have]


def _website(state: str, district: int) -> str | None:
    f = MEMBERS_DIR / f"{state}_{district}.json"
    if not f.exists():
        return None
    detail = json.loads(f.read_text()).get("member_detail") or {}
    return detail.get("official_website_url")


def _gather_one(candidate: dict, *, max_age_days: int, refresh: bool
                ) -> tuple[dict, str | None, str, list[str], str | None]:
    """Scrape half, read-through cached. Returns (candidate, website, text, sources, error).

    A fresh cached corpus is reused without touching the network — that is what makes adding a
    topic cheap and keeps us off house.gov's rate limiter for a re-score."""
    state, district = candidate["state"], candidate["district"]
    website = _website(state, district)
    if not website:
        return candidate, None, "", [], "no website on file"

    if not refresh:
        cached = _read_corpus(state, district, max_age_days)
        if cached and cached.get("text"):
            return candidate, cached.get("website") or website, cached["text"], cached.get("sources", []), None

    text, sources = gather_member_text(website)
    # No sources at all means the homepage fetch itself failed — the site was unreachable.
    # That is a DIFFERENT failure from a site we read but found too little on, and conflating
    # them hides a site-wide block behind hundreds of plausible-looking per-member skips.
    if not sources:
        return candidate, website, "", [], "site unreachable"
    if len(text) < 500:
        return candidate, website, text, sources, f"insufficient site text ({len(text)} chars)"

    # Persist the corpus BEFORE scoring, so a crawl is never wasted even if scoring fails.
    corpus_path(state, district).parent.mkdir(parents=True, exist_ok=True)
    corpus_path(state, district).write_text(json.dumps({
        "state": state, "district": district, "website": website,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources, "text": text,
    }, indent=2))
    return candidate, website, text, sources, None


def _score_one(candidate: dict, website: str, text: str, sources: list[str],
               llm: OpenRouterService, topics: list) -> tuple[dict, dict | None, str]:
    """Scoring half: LLM-score the stated stance on `topics` and merge into any existing file."""
    state, district = candidate["state"], candidate["district"]
    try:
        stances = score_stances(llm, text, topics)
    except Exception as e:
        return candidate, None, f"scoring failed: {e}"

    out = output_path(state, district)
    prior: dict = {}
    if out.exists():
        try:
            prior = json.loads(out.read_text())
        except (ValueError, OSError):
            prior = {}

    scored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Provenance per stance, not just per file — a file can span several runs once topics are
    # added incrementally, exactly as in s4_analysis.
    fresh = {
        s.topic_slug: {**s.model_dump(), "llm_model": llm.model,
                       "temperature": llm.temperature, "scored_at": scored_at}
        for s in stances
    }
    merged = {
        p["topic_slug"]: {**p,
                          "llm_model": p.get("llm_model", prior.get("llm_model")),
                          "temperature": p.get("temperature", prior.get("temperature"))}
        for p in prior.get("stances", [])
    }
    merged.update(fresh)

    result = {
        "state": state,
        "district": district,
        "bioguide_id": candidate.get("bioguide_id"),
        "website": website,
        # When the SITE was read (the corpus), distinct from when each stance was scored.
        "fetched_at": scored_at,
        "llm_model": llm.model,
        "temperature": llm.temperature,
        "source_pages": sources,
        "stances": list(merged.values()),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))

    n_addressed = sum(1 for v in merged.values() if v.get("addressed"))
    scoped = "" if len(topics) == len(TOPICS) else f" (+{len(topics)} new)"
    return candidate, result, f"{n_addressed}/{len(merged)} topics addressed{scoped}"


def run(candidates: list[dict], config: dict, *, force: bool = False):
    """Scrape (cached) then LLM-score each member's stated positions.

    Incremental like s4: by default a member is scored only on topics their file is missing,
    against a CACHED corpus. Adding a topic therefore costs one LLM call per member and no
    crawling. `--force` re-scores every topic; `--refresh-corpus` re-crawls the sites."""
    key = config.get("_openrouter_api_key")
    if not key or key == "put_key_here":
        print("  [!] OPENROUTER_API_KEY not set — cannot run stance extraction")
        return

    if not MEMBERS_DIR.exists():
        print("  [!] No members output found — run the 'members' stage first")
        return

    model = os.getenv("OPENROUTER_MODEL") or config.get("llm_model")
    llm = OpenRouterService(api_key=key, model=model) if model else OpenRouterService(api_key=key)
    print(f"  Using LLM model: {llm.model}")

    refresh_corpus = config.get("refresh_corpus", False)
    max_age_days = config.get("corpus_max_age_days", DEFAULT_CORPUS_MAX_AGE_DAYS)

    # A member is work only if some current topic has no stated stance on file.
    todo, topics_for = [], {}
    for c in candidates:
        missing = missing_topics_for(c["state"], c["district"], force=force)
        if missing:
            todo.append(c)
            topics_for[(c["state"], c["district"])] = missing
    print(f"  {len(candidates) - len(todo)} already cover all {len(TOPICS)} topics; "
          f"scoring stated positions for {len(todo)}")

    topup = [c for c in todo if len(topics_for[(c['state'], c['district'])]) < len(TOPICS)]
    if topup:
        print(f"  {len(topup)} of those need only the newly-added topics (existing stances kept)")

    cached = sum(1 for c in todo if not refresh_corpus
                 and _read_corpus(c["state"], c["district"], max_age_days))
    print(f"  Corpus cache: {cached}/{len(todo)} reusable"
          + (" — --refresh-corpus set, re-crawling all" if refresh_corpus else
             f" (re-crawling {len(todo) - cached})"))

    scrape_workers = min(config.get("positions_parallel", DEFAULT_SCRAPE_PARALLEL), max(1, len(todo)))
    score_workers = min(config.get("positions_score_parallel", DEFAULT_SCORE_PARALLEL), max(1, len(todo)))
    print(f"  Parallelism: {scrape_workers} scraping (house.gov limit), {score_workers} scoring (LLM)")

    done = errs = unreachable = 0
    # Pipelined: each member's scrape hands straight off to the scoring pool, so scoring runs
    # while later members are still being fetched instead of waiting for the whole crawl.
    with ThreadPoolExecutor(max_workers=scrape_workers) as scrapers, \
            ThreadPoolExecutor(max_workers=score_workers) as scorers:
        scrape_futures = {
            scrapers.submit(_gather_one, c, max_age_days=max_age_days,
                            refresh=refresh_corpus): c
            for c in todo
        }
        score_futures = {}

        scraped = 0
        for fut in as_completed(scrape_futures):
            candidate, website, text, sources, err = fut.result()
            scraped += 1
            # The scrape phase produces no per-member result lines (those come from scoring),
            # so without this the stage looks hung for its whole first half.
            if scraped % 25 == 0 or scraped == len(todo):
                print(f"  … scraped {scraped}/{len(todo)} member sites")
            if err:
                errs += 1
                if err == "site unreachable":
                    unreachable += 1
                print(f"  [skip] {candidate['name']} ({candidate['state']}-{candidate['district']}) "
                      f"— {err}  ({done + errs}/{len(todo)})")
                continue
            topics = topics_for[(candidate["state"], candidate["district"])]
            score_futures[
                scorers.submit(_score_one, candidate, website, text, sources, llm, topics)
            ] = candidate

        for fut in as_completed(score_futures):
            candidate, result, msg = fut.result()
            tag = f"{candidate['name']} ({candidate['state']}-{candidate['district']})"
            if result:
                done += 1
            else:
                errs += 1
            # Running spend on every member so a long run is trackable as it goes.
            print(f"  [{'done' if result else 'skip'}] {tag} — {msg}  · running ~${_spend(llm):.2f} "
                  f"({done + errs}/{len(todo)})")

    # A handful of unreachable sites is normal; a large share means WE were blocked, not that
    # hundreds of House sites went down at once. Fail loud rather than write a dataset whose
    # stated positions are silently missing.
    if todo and unreachable / len(todo) > 0.2:
        print(f"\n  [!!] {unreachable}/{len(todo)} member sites were UNREACHABLE "
              f"({unreachable / len(todo):.0%}). That is almost certainly rate limiting, not "
              f"broken sites — lower `positions_parallel` (currently {scrape_workers}) and "
              f"re-run with --force. The stance data from this run is NOT trustworthy.")

    print(f"  Stances: {done} scored, {errs} skipped ({unreachable} unreachable)")
    src = "OpenRouter-reported" if getattr(llm, "cost", 0.0) else "estimated"
    print(f"  Stances: {done} scored, {errs} skipped ({unreachable} unreachable)")
    print(f"  Spend: {llm.prompt_tokens:,} in + {llm.completion_tokens:,} out tokens "
          f"· ~${_spend(llm):.2f} total ({src})")
