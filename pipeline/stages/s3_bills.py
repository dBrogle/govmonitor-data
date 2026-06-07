"""Stage 3: Deep-dive bill data.

Fetches full detail for every bill that received a House roll-call vote this congress
(plus bills referenced by tracked members and a recent-bills sample), and the full roll
call member positions for those votes. Bill and vote fetches run in parallel
(config.bills_parallel) since each is independent and I/O-bound.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from services.congress.congress import CongressService

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s3_bills"
VOTES_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s3_votes"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"

DEFAULT_BILLS_PARALLEL = 10


def output_path(congress: int, bill_type: str, bill_number: str) -> Path:
    return OUTPUT_DIR / f"{congress}_{bill_type}_{bill_number}.json"


def vote_output_path(congress: int, session: int, vote_number: int) -> Path:
    return VOTES_OUTPUT_DIR / f"{congress}_{session}_{vote_number}.json"


def _collect_bill_keys_from_members() -> set[tuple[int, str, str]]:
    """Read all member outputs and collect unique (congress, type, number) bill keys."""
    keys: set[tuple[int, str, str]] = set()
    if not MEMBERS_DIR.exists():
        return keys

    for member_file in MEMBERS_DIR.glob("*.json"):
        data = json.loads(member_file.read_text())
        for bill in data.get("sponsored_bills", []):
            keys.add((bill["congress"], bill["type"], str(bill["number"])))
        for bill in data.get("cosponsored_bills", []):
            keys.add((bill["congress"], bill["type"], str(bill["number"])))
        for vote in data.get("voting_history", []):
            bill = vote.get("bill")
            if bill and bill.get("type") and bill.get("number"):
                keys.add((
                    data.get("member_detail", {}).get("congress", bill.get("congress", 119)),
                    bill["type"],
                    str(bill["number"]),
                ))
    return keys


def _process_one_bill(svc: CongressService, congress: int, bill_type: str, bill_number: str):
    """Fetch + write one bill's full detail. Returns (tag, status, error_message)."""
    tag = f"{bill_type} {bill_number}"
    try:
        detail = svc.get_bill_detail(congress, bill_type, bill_number)
        if not detail:
            return tag, "not_found", None

        amendments = svc.get_bill_amendments(congress, bill_type, bill_number)
        committees = svc.get_bill_committees(congress, bill_type, bill_number)
        cosponsors = svc.get_bill_cosponsors(congress, bill_type, bill_number)
        related = svc.get_bill_related_bills(congress, bill_type, bill_number)
        subjects = svc.get_bill_subjects(congress, bill_type, bill_number)
        titles = svc.get_bill_titles(congress, bill_type, bill_number)
        text_versions = svc.get_bill_text(congress, bill_type, bill_number)

        has_xml = False
        try:
            has_xml = svc.get_bill_text_xml(congress, bill_type, bill_number) is not None
        except Exception:
            pass

        result = {
            "congress": congress,
            "bill_type": bill_type,
            "bill_number": bill_number,
            "detail": detail.model_dump(),
            "amendments": [a.model_dump() for a in amendments],
            "committees": [c.model_dump() for c in committees],
            "cosponsors": [c.model_dump() for c in cosponsors],
            "related_bills": [r.model_dump() for r in related],
            "subjects": [s.model_dump() for s in subjects],
            "titles": [t.model_dump() for t in titles],
            "text_versions": [t.model_dump() for t in text_versions],
            "has_xml": has_xml,
        }
        out = output_path(congress, bill_type, bill_number)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        return tag, "done", None
    except Exception as e:
        return tag, "error", str(e)


def _process_one_vote(svc: CongressService, congress: int, session: int, vote, bill_ref: dict):
    """Fetch + write one roll-call vote's member positions. Returns (tag, status, error)."""
    tag = f"vote {session}-{vote.vote_number}"
    try:
        members = svc.get_vote_members(congress, session, vote.vote_number)
        result = {
            "congress": congress,
            "session": session,
            "vote_number": vote.vote_number,
            "vote_date": vote.vote_date,
            "vote_question": vote.vote_question,
            "vote_result": vote.vote_result,
            "bill": bill_ref,
            "members": [
                {
                    "bioguide_id": m.bioguide_id,
                    "first_name": m.first_name,
                    "last_name": m.last_name,
                    "party": m.party,
                    "state": m.state,
                    "position": m.vote_position,
                }
                for m in members
            ],
        }
        out = vote_output_path(congress, session, vote.vote_number)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        return tag, "done", None
    except Exception as e:
        return tag, "error", str(e)


def _run_parallel(items, fn, *, workers: int, label: str):
    """Run fn over items in parallel batches, tallying done/error. Returns (done, errors)."""
    done = errors = 0
    total = len(items)
    for start in range(0, total, workers):
        batch = items[start : start + workers]
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [executor.submit(fn, item) for item in batch]
            for future in as_completed(futures):
                tag, status, error = future.result()
                if status == "done":
                    done += 1
                elif error:
                    errors += 1
                    print(f"    {tag}: {error}")
        print(f"  [{label}] {min(start + workers, total)}/{total} ({done} done, {errors} errors)")
    return done, errors


def run(candidates: list[dict], config: dict, *, force: bool = False):
    congress = config["congress"]
    recent_bills_limit = config.get("recent_bills_limit", 20)
    workers = config.get("bills_parallel", DEFAULT_BILLS_PARALLEL)
    # Cover every session of this congress so all voted bills are captured.
    sessions = config.get("sessions") or [config.get("session", 1)]

    svc = CongressService(api_key=config["_congress_api_key"])

    # ── Collect the bill set ─────────────────────────────────────────────
    bill_keys = _collect_bill_keys_from_members()
    print(f"  {len(bill_keys)} bills from member outputs")

    # Every bill that received a House roll-call vote (the substantive, alignment-relevant
    # set). Keep the votes around so we can fetch their member positions below.
    votes_by_session: dict[int, list] = {}
    for session in sessions:
        try:
            votes = svc.get_house_votes(congress, session)
        except Exception as e:
            print(f"  [warning] votes for session {session}: {e}")
            votes = []
        votes_by_session[session] = votes
        before = len(bill_keys)
        for v in votes:
            b = v.bill
            if b and b.get("type") and b.get("number"):
                bill_keys.add((congress, b["type"], str(b["number"])))
        print(f"  + session {session}: {len(votes)} votes → {len(bill_keys) - before} new voted bills")

    recent_bills = svc.get_bills(congress=congress, limit=recent_bills_limit)
    for b in recent_bills:
        bill_keys.add((b.congress, b.type, str(b.number)))
    print(f"  + {len(recent_bills)} recent → {len(bill_keys)} total unique bills")

    # ── Fetch bill details (parallel) ────────────────────────────────────
    to_process = []
    skipped = 0
    for bc, bt_raw, bn in sorted(bill_keys):
        bt = bt_raw.upper()
        if output_path(bc, bt, bn).exists() and not force:
            skipped += 1
        else:
            to_process.append((bc, bt, bn))
    print(f"\n  Bills: {skipped} already on disk, {len(to_process)} to fetch ({workers}-way parallel)")

    if to_process:
        done, errors = _run_parallel(
            to_process,
            lambda item: _process_one_bill(svc, item[0], item[1], item[2]),
            workers=workers,
            label="bills",
        )
        print(f"  Bills: {done} processed, {skipped} skipped, {errors} errors")

    # ── Fetch roll call vote member positions (parallel) ─────────────────
    # Every voted bill is now tracked, so fetch member positions for all such votes.
    bill_lookup = {(bt.upper(), bn) for (_, bt, bn) in bill_keys}
    vote_jobs = []
    for session, votes in votes_by_session.items():
        for vote in votes:
            if vote_output_path(congress, session, vote.vote_number).exists() and not force:
                continue
            b = vote.bill
            if not b:
                continue
            if ((b.get("type") or "").upper(), str(b.get("number") or "")) in bill_lookup:
                vote_jobs.append((session, vote, b))

    print(f"\n  Votes: {len(vote_jobs)} roll calls to fetch ({workers}-way parallel)")
    if vote_jobs:
        done, errors = _run_parallel(
            vote_jobs,
            lambda job: _process_one_vote(svc, congress, job[0], job[1], job[2]),
            workers=workers,
            label="votes",
        )
        print(f"  Votes: {done} processed, {errors} errors")
