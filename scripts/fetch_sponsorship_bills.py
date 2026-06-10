"""Bulk-fetch detail + text for sponsored/cosponsored bills (alignment-quality work).

Enumerates the sponsorship universe from the member outputs, keeps only bills introduced
within the recency window (default: the past year), and fetches each bill's full detail
into the s3_bills cache — reusing the tested stage fetch path. Resumable: bills already on
disk are skipped, so it's safe to interrupt and re-run.

Run from the data/ directory:
    python scripts/fetch_sponsorship_bills.py
    python scripts/fetch_sponsorship_bills.py --since 2025-06-08 --workers 30

Designed to run unattended in a separate session while scoring logic is built in parallel.
It only populates the cache; nothing else reads from it mid-run.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from pipeline.stages.s3_bills import _process_one_bill, _run_parallel, output_path
from services.congress.congress import CongressService

DATA_ROOT = Path(__file__).parent.parent
MEMBERS_DIR = DATA_ROOT / "pipeline" / "output" / "s1_members"

# Default recency window: one year before 2026-06-08 (the date this work began). Bills
# introduced earlier are out of scope for now (the 119th Congress started Jan 2025; the
# window only starts mattering once prior congresses are added). See ALIGNMENT_QUALITY_PLAN.md.
DEFAULT_SINCE = "2025-06-08"


def _load_api_keys() -> list[str]:
    """Collect api.data.gov keys: numbered CONGRESS_API_KEY_1.. with single-key fallback.

    Mirrors pipeline/run.py so this script uses the same key pool (round-robined by the
    service for ~N× throughput against the 5,000-req/hr-per-key limit)."""
    keys: list[str] = []
    i = 1
    while True:
        k = os.getenv(f"CONGRESS_API_KEY_{i}")
        if not k or k == "put_key_here":
            break
        keys.append(k)
        i += 1
    if not keys:
        single = os.getenv("CONGRESS_API_KEY")
        if single and single != "put_key_here":
            keys.append(single)
    return keys


def _collect_recent_sponsorship_keys(since: str) -> list[tuple[int, str, str]]:
    """Unique (congress, TYPE, number) for sponsored/cosponsored bills introduced >= `since`.

    Cosponsored-only adds almost nothing (nearly every cosponsored bill is also someone's
    sponsored bill), so the union is effectively the sponsored set."""
    keys: set[tuple[int, str, str]] = set()
    for member_file in MEMBERS_DIR.glob("*.json"):
        data = json.loads(member_file.read_text())
        for bill in data.get("sponsored_bills", []) + data.get("cosponsored_bills", []):
            introduced = bill.get("introduced_date")
            if not introduced or introduced < since:
                continue
            keys.add((bill["congress"], bill["type"].upper(), str(bill["number"])))
    return sorted(keys)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help=f"Only fetch bills introduced on/after this date (default {DEFAULT_SINCE})")
    ap.add_argument("--workers", type=int, default=30,
                    help="Parallel fetch workers (default 30; throughput caps at keys × 5k/hr)")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if the bill is already on disk")
    args = ap.parse_args()

    load_dotenv(DATA_ROOT / ".env")
    api_keys = _load_api_keys()
    if not api_keys:
        sys.exit("  [!] No CONGRESS_API_KEY(_n) found in .env — cannot fetch.")
    print(f"  API keys in pool: {len(api_keys)}  (~{len(api_keys) * 5000} req/hr ceiling)")

    if not MEMBERS_DIR.exists():
        sys.exit("  [!] No member outputs found — run the 'members' stage first.")

    svc = CongressService(api_key=api_keys)

    keys = _collect_recent_sponsorship_keys(args.since)
    to_fetch = [k for k in keys if args.force or not output_path(*k).exists()]
    skipped = len(keys) - len(to_fetch)
    print(f"  Sponsorship bills introduced >= {args.since}: {len(keys)}")
    print(f"  {skipped} already on disk, {len(to_fetch)} to fetch ({args.workers}-way parallel)\n")

    if not to_fetch:
        print("  Nothing to do.")
        return

    start = time.monotonic()
    done, errors = _run_parallel(
        to_fetch,
        lambda item: _process_one_bill(svc, item[0], item[1], item[2]),
        workers=args.workers,
        label="bills",
    )
    mins, secs = divmod(int(time.monotonic() - start), 60)
    print(f"\n  Done: {done} fetched, {skipped} skipped, {errors} errors  ({mins}m {secs}s)")


if __name__ == "__main__":
    main()
