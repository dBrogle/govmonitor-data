"""Stage 1: Fetch congressional member profiles, legislation, and voting history.

Members are processed in parallel (config.members_parallel) — each is independent and
I/O-bound (Congress.gov calls), so concurrency converts per-request latency into
throughput up to the API's hourly rate cap. The shared CongressService retries 429s
and transient network errors with backoff, so overshooting the cap self-throttles
rather than failing.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from services.congress.congress import CongressService

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s1_members"
DEFAULT_MEMBERS_PARALLEL = 8


def output_path(state: str, district: int) -> Path:
    return OUTPUT_DIR / f"{state}_{district}.json"


def _run_parallel(items, fn, *, workers, label):
    """Run fn over items in parallel batches, tallying done/skip/error."""
    done = skipped = errors = 0
    total = len(items)
    for start in range(0, total, workers):
        batch = items[start : start + workers]
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [executor.submit(fn, item) for item in batch]
            for future in as_completed(futures):
                tag, status, error = future.result()
                if status == "done":
                    done += 1
                elif status == "skip":
                    skipped += 1
                elif error:
                    errors += 1
                    print(f"    [!] {tag}: {error}")
        print(f"  [{label}] {min(start + workers, total)}/{total} "
              f"({done} done, {skipped} skipped, {errors} errors)")
    return done, errors


def _process_one(svc, candidate, *, congress, vote_index, sponsored_limit,
                 cosponsored_limit, vote_limit, force):
    state = candidate["state"]
    district = candidate["district"]
    name = candidate["name"]
    label = f"{name} ({state}-{district})"
    out = output_path(state, district)

    if out.exists() and not force:
        return label, "skip", None

    # Guard each candidate so one failure (e.g. an exhausted-retry network error)
    # doesn't abort the whole batch — the rest still process.
    try:
        # Prefer the authoritative bioguide from the roster (build_roster.py pulls it from
        # the congress-legislators dataset). The district endpoint is unreliable for seats
        # that changed hands mid-term — it returns both members, and picking the wrong one
        # attaches the former occupant (e.g. GA-14 resolved to Greene instead of Fuller).
        bioguide_id = candidate.get("bioguide_id")
        if not bioguide_id:
            members = svc.get_members_by_district(congress, state, district)
            if not members:
                return label, "error", f"no member found for {state}-{district} in {congress}th"
            # Multiple may come back (incl. historical); pick the one whose detail shows a
            # term matching the requested congress + district, else the first.
            member_summary = members[0]
            if len(members) > 1:
                for ms in members:
                    detail_check = svc.get_member(ms.bioguide_id)
                    if detail_check and detail_check.terms and any(
                        t.get("congress") == congress and t.get("district") == district
                        for t in detail_check.terms
                    ):
                        member_summary = ms
                        break
            bioguide_id = member_summary.bioguide_id

        detail = svc.get_member(bioguide_id)
        sponsored = svc.get_sponsored_legislation(bioguide_id, congress=congress)
        cosponsored = svc.get_cosponsored_legislation(bioguide_id, congress=congress)
    except Exception as e:
        return label, "error", str(e)

    # Already most-recent-first, across every session of this congress. Kept whole by
    # default: alignment scores off the full record, and truncating here would silently
    # narrow the evidence base to the last few months of voting.
    votes = vote_index.get(bioguide_id, [])
    if vote_limit:
        votes = votes[:vote_limit]

    result = {
        "bioguide_id": bioguide_id,
        "name": name,
        "state": state,
        "district": district,
        "party": detail.current_party if detail else None,
        "member_detail": detail.model_dump() if detail else None,
        "sponsored_bills": [b.model_dump() for b in sponsored[:sponsored_limit]],
        "cosponsored_bills": [b.model_dump() for b in cosponsored[:cosponsored_limit]],
        "voting_history": votes,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))
    return label, "done", None


def run(candidates: list[dict], config: dict, *, force: bool = False):
    congress = config["congress"]
    # Cover every session of this congress. Reading a scalar `session` here (while
    # s3_bills read the `sessions` list) is what kept an entire year of roll calls
    # out of every member's voting history — and therefore out of alignment.
    sessions = config.get("sessions") or [config.get("session", 1)]
    sponsored_limit = config.get("sponsored_bill_limit", 50)
    cosponsored_limit = config.get("cosponsored_bill_limit", 50)
    # Falsy (null/absent) keeps the full voting record; set an int only to cap it.
    vote_limit = config.get("vote_limit")
    workers = config.get("members_parallel", DEFAULT_MEMBERS_PARALLEL)

    svc = CongressService(api_key=config["_congress_api_key"])

    # Built once and shared: resolving votes per member would re-parse every cached
    # roll call 430 times. Must precede the parallel loop — the threads only read it.
    print(f"  Indexing roll-call votes for sessions {sessions}...")
    vote_index = svc.get_member_votes_index(congress, sessions)
    total_votes = sum(len(v) for v in vote_index.values())
    print(f"  Indexed {total_votes} member-votes across {len(vote_index)} members")

    def fn(candidate):
        return _process_one(
            svc, candidate, congress=congress, vote_index=vote_index,
            sponsored_limit=sponsored_limit, cosponsored_limit=cosponsored_limit,
            vote_limit=vote_limit, force=force,
        )

    print(f"  Processing {len(candidates)} members ({workers}-way parallel)...")
    _run_parallel(candidates, fn, workers=workers, label="members")
