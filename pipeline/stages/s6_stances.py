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

from services.llm.openrouter import OpenRouterService
from services.positions.scraper import gather_member_text
from services.positions.stances import score_stances

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s6_stances"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"

DEFAULT_PARALLEL = 6

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


def _website(state: str, district: int) -> str | None:
    f = MEMBERS_DIR / f"{state}_{district}.json"
    if not f.exists():
        return None
    detail = json.loads(f.read_text()).get("member_detail") or {}
    return detail.get("official_website_url")


def _process_one(candidate: dict, llm: OpenRouterService) -> tuple[dict, dict | None, str]:
    state, district = candidate["state"], candidate["district"]
    website = _website(state, district)
    if not website:
        return candidate, None, "no website on file"

    text, sources = gather_member_text(website)
    if len(text) < 500:
        return candidate, None, f"insufficient site text ({len(text)} chars)"

    try:
        stances = score_stances(llm, text)
    except Exception as e:
        return candidate, None, f"scoring failed: {e}"

    result = {
        "state": state,
        "district": district,
        "bioguide_id": candidate.get("bioguide_id"),
        "website": website,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm_model": llm.model,
        "temperature": llm.temperature,
        "source_pages": sources,
        "stances": [s.model_dump() for s in stances],
    }
    out = output_path(state, district)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))

    n_addressed = sum(1 for s in stances if s.addressed)
    return candidate, result, f"{n_addressed}/{len(stances)} topics addressed"


def run(candidates: list[dict], config: dict, *, force: bool = False):
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

    todo = [c for c in candidates if force or not output_path(c["state"], c["district"]).exists()]
    print(f"  {len(candidates) - len(todo)} already done; scoring stated positions for {len(todo)}")

    workers = min(config.get("positions_parallel", DEFAULT_PARALLEL), max(1, len(todo)))
    done = errs = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_one, c, llm): c for c in todo}
        for fut in as_completed(futures):
            candidate, result, msg = fut.result()
            tag = f"{candidate['name']} ({candidate['state']}-{candidate['district']})"
            label = "done" if result else "skip"
            if result:
                done += 1
            else:
                errs += 1
            # Running spend on every member so a long run is trackable as it goes.
            print(f"  [{label}] {tag} — {msg}  · running ~${_spend(llm):.2f} "
                  f"({done + errs}/{len(todo)})")

    src = "OpenRouter-reported" if getattr(llm, "cost", 0.0) else "estimated"
    print(f"  Stances: {done} scored, {errs} skipped")
    print(f"  Spend: {llm.prompt_tokens:,} in + {llm.completion_tokens:,} out tokens "
          f"· ~${_spend(llm):.2f} total ({src})")
