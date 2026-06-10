"""Fetch committee assignments + party-leadership roles for tracked members.

Congress.gov's member endpoint does NOT expose committee membership or leadership, so we
pull the canonical, well-maintained `unitedstates/congress-legislators` dataset (JSON):
  - committee-membership-current.json  → who sits on what, with chair/ranking-member rank
  - legislators-current.json           → party-leadership roles (Speaker, Whips, etc.)

These feed the bill **impact score** (a sponsor's institutional power is the single best
predictor of whether their bill moves — committee chairs control their jurisdiction's
calendar; rank-and-file members can't force a floor vote). See ALIGNMENT_QUALITY_PLAN.md.

Writes pipeline/output/member_roles.json keyed by bioguide. Fast (two downloads) and
independent of the bill fetch, so it can run anytime. Run from data/:
    python scripts/fetch_member_roles.py
"""

import json
import sys
from pathlib import Path

import requests

DATA_ROOT = Path(__file__).parent.parent
MEMBERS_DIR = DATA_ROOT / "pipeline" / "output" / "s1_members"
RAW_DIR = DATA_ROOT / "data" / "congress_legislators"
OUT_PATH = DATA_ROOT / "pipeline" / "output" / "member_roles.json"

BASE = "https://unitedstates.github.io/congress-legislators"
SOURCES = ["committee-membership-current", "legislators-current"]

# Leadership/committee titles ranked by power, highest first. Used to pick a member's single
# `top_role` (the strongest predictor of bill-moving ability) from all their roles.
LEADERSHIP_TITLES = ("speaker", "majority leader", "minority leader", "majority whip",
                     "minority whip")


def _download(name: str) -> dict | list:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_DIR / f"{name}.json"
    r = requests.get(f"{BASE}/{name}.json", timeout=60)
    r.raise_for_status()
    cache.write_text(r.text)
    return r.json()


def _is_full_committee(cid: str) -> bool:
    """House/Senate full-committee ids are 4 chars (e.g. HSAS); subcommittees append digits."""
    return len(cid) == 4


def main():
    if not MEMBERS_DIR.exists():
        sys.exit("  [!] No member outputs found — run the 'members' stage first.")

    tracked = set()
    for f in MEMBERS_DIR.glob("*.json"):
        bid = json.loads(f.read_text()).get("bioguide_id")
        if bid:
            tracked.add(bid)
    print(f"  Tracked members: {len(tracked)}")

    committees = _download("committee-membership-current")  # {committee_id: [member,...]}
    legislators = _download("legislators-current")

    # bioguide -> committee roles
    roles: dict[str, dict] = {bid: {"committees": [], "leadership": [],
                                    "is_chair": False, "is_ranking": False} for bid in tracked}
    for cid, members in committees.items():
        for m in members:
            bid = m.get("bioguide")
            if bid not in roles:
                continue
            title = (m.get("title") or "").strip()
            full = _is_full_committee(cid)
            roles[bid]["committees"].append({
                "committee_id": cid,
                "is_full_committee": full,
                "title": title,            # "Chairman" / "Ranking Member" / "Vice Chair" / ""
                "rank": m.get("rank"),
                "party": m.get("party"),   # majority / minority
            })
            tl = title.lower()
            if full and ("chair" in tl):
                roles[bid]["is_chair"] = True
            if full and ("ranking" in tl):
                roles[bid]["is_ranking"] = True

    # bioguide -> active party-leadership roles
    for leg in legislators:
        bid = leg.get("id", {}).get("bioguide")
        if bid not in roles:
            continue
        for lr in leg.get("leadership_roles", []):
            if lr.get("end"):       # only currently-active leadership roles
                continue
            roles[bid]["leadership"].append({"title": lr.get("title"), "chamber": lr.get("chamber")})

    # Single `top_role` per member — the strongest power signal, for ranking/weighting.
    for bid, r in roles.items():
        lead_titles = " ".join((l.get("title") or "").lower() for l in r["leadership"])
        if any(t in lead_titles for t in LEADERSHIP_TITLES) or r["leadership"]:
            r["top_role"] = "Party Leadership"
        elif r["is_chair"]:
            r["top_role"] = "Committee Chair"
        elif r["is_ranking"]:
            r["top_role"] = "Ranking Member"
        elif any("chair" in (c["title"] or "").lower() for c in r["committees"]):
            r["top_role"] = "Subcommittee Chair"
        elif r["committees"]:
            r["top_role"] = "Committee Member"
        else:
            r["top_role"] = "None"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(roles, indent=2))

    from collections import Counter
    dist = Counter(r["top_role"] for r in roles.values())
    matched = sum(1 for r in roles.values() if r["committees"] or r["leadership"])
    print(f"  Roles resolved for {matched}/{len(tracked)} tracked members")
    print(f"  Top-role distribution: {dict(dist)}")
    print(f"  → {OUT_PATH.relative_to(DATA_ROOT)}")


if __name__ == "__main__":
    main()
