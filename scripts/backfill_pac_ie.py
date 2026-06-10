"""Backfill outside_spending (Schedule E) into existing PAC profiles without re-running s2.

For each cached PAC profile, fetch the committee's independent expenditures — which candidates
it spent to support/oppose — and merge the summary in. Idempotent: skips already-enriched
profiles unless --force. New profiles get this automatically via build_pac_profile.

    python data/scripts/backfill_pac_ie.py [--force] [--workers N]
"""

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "data"))
from dotenv import load_dotenv  # noqa: E402
from services.campaign_finance.fec import FECService  # noqa: E402
from pipeline.stages.s2_finance import build_outside_spending  # noqa: E402

load_dotenv(os.path.join(REPO, "data", ".env"))
PROFILES = os.path.join(REPO, "data", "pipeline", "output", "s2_finance", "pac_profiles")


def load_keys() -> list[str]:
    keys: list[str] = []
    i = 1
    while True:
        k = os.getenv(f"CONGRESS_API_KEY_{i}")
        if not k or k == "put_key_here":
            break
        keys.append(k)
        i += 1
    if not keys:
        for n in ("OPEN_FEC_API_KEY", "CONGRESS_API_KEY"):
            v = os.getenv(n)
            if v:
                keys.append(v)
    return keys or ["DEMO_KEY"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    keys = load_keys()
    svc = FECService(api_key=keys)
    files = glob.glob(os.path.join(PROFILES, "*.json"))
    todo = [f for f in files if args.force or "outside_spending" not in json.load(open(f))]
    print(f"{len(files)} profiles, {len(todo)} to enrich ({len(keys)} keys, {args.workers} workers)")

    def work(f: str) -> int:
        d = json.load(open(f))
        os_ = build_outside_spending(svc, d["committee_id"], d["cycle"])
        d["outside_spending"] = os_
        json.dump(d, open(f, "w"), indent=2, default=str)
        return 1 if os_ else 0

    done = with_ie = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(work, f) for f in todo]
        for fut in as_completed(futures):
            done += 1
            with_ie += fut.result()
            if done % 100 == 0:
                print(f"  {done}/{len(todo)}")
    print(f"done: {done} processed, {with_ie} have outside spending")


if __name__ == "__main__":
    main()
