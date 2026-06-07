"""
Test script: Pull campaign funding data from OpenFEC for 3 congress members.
API docs: https://api.open.fec.gov/developers/
Get a free API key at: https://api.data.gov/signup/
Set FEC_API_KEY in a .env file or environment variable.
"""

import os
import json
import time
import hashlib
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPEN_FEC_API_KEY", "DEMO_KEY")
BASE_URL = "https://api.open.fec.gov/v1"
CACHE_DIR = Path(__file__).parent / "data"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(url: str, params: dict) -> Path:
    """Stable cache filename based on URL + params (excluding api_key)."""
    key_params = {k: v for k, v in sorted(params.items()) if k != "api_key"}
    fingerprint = json.dumps({"url": url, "params": key_params}, sort_keys=True)
    digest = hashlib.sha1(fingerprint.encode()).hexdigest()[:12]
    # human-readable prefix from the URL path
    slug = url.replace(BASE_URL, "").strip("/").replace("/", "_")
    return CACHE_DIR / f"{slug}__{digest}.json"


def _load_cache(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _save_cache(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))

# Three well-known congress members with their FEC candidate IDs
# Format: (name, candidate_id)
# Find IDs at: https://www.fec.gov/data/candidates/
CANDIDATES = [
    ("Alexandria Ocasio-Cortez", "H8NY15148"),  # D - NY-14
    ("Marjorie Taylor Greene",   "H0GA14050"),  # R - GA-14
    ("Nancy Pelosi",             "H8CA05035"),  # D - CA-11
]


def fec_get(url: str, params: dict, retries: int = 3) -> dict:
    """GET wrapper with disk cache, retry on 429, and timeout handling."""
    cache_file = _cache_path(url, params)
    cached = _load_cache(cache_file)
    if cached is not None:
        print(f"    [cache hit: {cache_file.name}]")
        return cached

    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = 2 ** attempt * 5  # 5s, 10s, 20s
                print(f"    [rate limited, waiting {wait}s...]")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            _save_cache(cache_file, data)
            print(f"    [cached: {cache_file.name}]")
            return data
        except requests.exceptions.Timeout:
            raise
    raise RuntimeError(
        f"Rate limited after {retries} retries. "
        "Get a free API key at https://api.data.gov/signup/ and set FEC_API_KEY in a .env file."
    )


def get_candidate_totals(candidate_id: str, cycle: int = 2024) -> dict:
    """Fetch total receipts & disbursements for a candidate in a given election cycle."""
    url = f"{BASE_URL}/candidates/totals/"
    params = {"candidate_id": candidate_id, "cycle": cycle, "api_key": API_KEY}
    data = fec_get(url, params)
    results = data.get("results", [])
    return results[0] if results else {}


def get_top_donors(candidate_id: str, cycle: int = 2024, limit: int = 5) -> list:
    """Fetch top individual contributors for a candidate's principal committee."""
    # First find their principal committee
    url = f"{BASE_URL}/candidate/{candidate_id}/committees/"
    params = {"cycle": cycle, "api_key": API_KEY, "designation": "P"}
    committees = fec_get(url, params).get("results", [])
    if not committees:
        return []

    committee_id = committees[0]["committee_id"]

    url = f"{BASE_URL}/schedules/schedule_a/"
    params = {
        "committee_id": committee_id,
        "two_year_transaction_period": cycle,
        "sort": "-contribution_receipt_amount",
        "per_page": limit,
        "api_key": API_KEY,
    }
    return fec_get(url, params).get("results", [])


def summarize_donor(donor: dict) -> dict:
    return {
        "contributor_name": donor.get("contributor_name"),
        "contributor_employer": donor.get("contributor_employer"),
        "contributor_occupation": donor.get("contributor_occupation"),
        "amount": donor.get("contribution_receipt_amount"),
        "date": donor.get("contribution_receipt_date"),
    }


def main():
    cycle = 2024
    print(f"\n{'='*60}")
    print(f"  OpenFEC Funding Snapshot — {cycle} Cycle")
    print(f"{'='*60}\n")

    for name, cid in CANDIDATES:
        print(f"--- {name} ({cid}) ---")

        totals = get_candidate_totals(cid, cycle)
        if totals:
            print(f"  Total raised:       ${totals.get('receipts', 0):>15,.2f}")
            print(f"  Total spent:        ${totals.get('disbursements', 0):>15,.2f}")
            print(f"  Cash on hand:       ${totals.get('cash_on_hand_end_period', 0):>15,.2f}")
            print(f"  Individual contribs:${totals.get('individual_itemized_contributions', 0):>15,.2f}")
            print(f"  PAC money:          ${totals.get('other_political_committee_contributions', 0):>15,.2f}")
        else:
            print("  (no totals found for this cycle)")

        print(f"\n  Top {5} individual donations:")
        try:
            donors = get_top_donors(cid, cycle)
            if donors:
                for d in donors:
                    s = summarize_donor(d)
                    print(f"    ${s['amount']:>10,.2f}  {s['contributor_name']}"
                          f"  [{s['contributor_occupation']} @ {s['contributor_employer']}]  {s['date']}")
            else:
                print("    (none found)")
        except requests.exceptions.Timeout:
            print("    (schedule_a timed out — common with DEMO_KEY, use a real API key)")
        except Exception as e:
            print(f"    (error: {e})")

        time.sleep(1)  # be polite to the API

        if totals:
            print(f"\n  Raw totals keys available:")
            for k, v in sorted(totals.items()):
                if v not in (None, 0, False, ""):
                    print(f"    {k}: {v}")

        print()


if __name__ == "__main__":
    main()
