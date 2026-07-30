"""Build candidates.json for the full voting US House (435 seats).

Source: the @unitedstates/congress-legislators dataset (same project we pull member
images from). It carries curated FEC IDs + state/district/party for every currently
serving member, so we avoid fuzzy FEC name-matching entirely.

Only the 435 *voting* representatives are included — the 50 states' House seats.
Non-voting delegates / the resident commissioner (DC, PR, and the territories) and all
senators are excluded. Vacant seats simply won't appear (the dataset lists only sitting
members), so a count below 435 means there are current vacancies.

Existing candidates.json entries are preserved: if a seat already has a hand-verified
fec_id, we keep it (it's known-good and keeps the s2_finance cache warm).

    python data/scripts/build_roster.py [--dry-run]
"""

import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(REPO, "data", "pipeline", "candidates.json")
SOURCE = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

# Reuse the pipeline's FEC client (and .env) to disambiguate members with more than
# one FEC registration by checking which one actually raised money this cycle.
sys.path.insert(0, os.path.join(REPO, "data"))
from dotenv import load_dotenv  # noqa: E402
from services.campaign_finance.fec import FECService  # noqa: E402

load_dotenv(os.path.join(REPO, "data", ".env"))
CYCLE = 2024

# The 50 states with voting House representation. Excludes DC and the territories
# (AS, GU, MP, PR, VI), whose members are non-voting delegates / a resident commissioner.
STATES_50 = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


def pick_house_fec(
    fec_ids: list[str], state: str, district: int, fec: "FECService | None"
) -> tuple[str | None, str | None]:
    """Choose the member's active House (H-prefixed) FEC candidate ID.

    A member can carry more than one H-prefixed ID (redistricting, re-files). The embedded
    district is NOT reliable for picking — FEC encodes the district at *first* registration,
    so a member's active committee often shows an old district, while a dead old committee can
    coincidentally match their current number. So for multi-ID members we pick purely by which
    committee actually raised money (this cycle or the next). Returns (fec_id, flag)."""
    house = [f for f in fec_ids if f.startswith("H")]
    if not house:
        return None, "no-house-fec-id"
    if len(house) == 1:
        return house[0], None

    if fec is not None:
        # Pick the registration with the most receipts. Check the election cycle and the
        # next one — special-election / freshman money often sits under the 2026 cycle.
        def receipts(cid: str) -> float:
            best = 0.0
            for cy in (CYCLE, CYCLE + 2):
                t = fec.get_candidate_totals(cid, cy)
                if t and t.receipts and t.receipts > best:
                    best = t.receipts
            return best

        scored = [(f, receipts(f)) for f in house]
        best, amount = max(scored, key=lambda x: x[1])
        if amount > 0:
            return best, f"resolved-by-receipts (${amount:,.0f})"

    # No FEC client, or no receipts anywhere — fall back to a current-district match.
    dd = f"{district:02d}"
    seat = [f for f in house if f[2:4] == state and f[4:6] == dd]
    return (seat or house)[-1], "AMBIGUOUS — verify manually"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print summary without writing")
    args = parser.parse_args()

    print(f"Fetching {SOURCE} ...")
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "watchgov"})
    legislators = json.load(urllib.request.urlopen(req, timeout=120))
    print(f"  {len(legislators)} current legislators")

    # api.data.gov keys are universal — one key works for Congress.gov and OpenFEC alike,
    # so fall back to the pipeline's key pool rather than requiring a separate variable.
    key = os.getenv("OPEN_FEC_API_KEY") or os.getenv("CONGRESS_API_KEY_1")
    fec = FECService(api_key=key) if key else None
    if fec is None:
        print("  [!] No OPEN_FEC_API_KEY / CONGRESS_API_KEY_1 — can't disambiguate multi-ID members by activity")

    # Hand-verified fec_ids from the existing roster, keyed by seat, alongside the bioguide
    # they belonged to. The bioguide — not the fec_id — is what tells us whether a seat
    # changed hands: upstream prunes stale FEC registrations from a sitting member's id list
    # fairly often, and those pruned ids are frequently the only ones carrying receipts.
    existing: dict[tuple[str, int], tuple[str, str | None]] = {}
    if os.path.exists(OUT):
        for c in json.load(open(OUT)):
            if c.get("fec_id"):
                existing[(c["state"], c["district"])] = (c["fec_id"], c.get("bioguide_id"))

    roster: list[dict] = []
    flags: list[str] = []

    for L in legislators:
        term = L["terms"][-1]
        if term["type"] != "rep" or term["state"] not in STATES_50:
            continue

        state = term["state"]
        district = term["district"]  # 0 for at-large
        name = L["name"].get("official_full") or f"{L['name']['first']} {L['name']['last']}"
        member_fecs = L["id"].get("fec", [])
        bioguide = L["id"]["bioguide"]
        seat = f"{state}-{district}"

        kept, kept_bioguide = existing.get((state, district), (None, None))
        same_member = kept_bioguide is not None and kept_bioguide == bioguide

        if kept and kept in member_fecs:
            # Our verified ID is one of this member's registrations — trust it (warm cache).
            fec_id = kept
        elif kept and same_member:
            # Same person, but upstream no longer lists the ID we verified. Switching on that
            # alone has blanked finance before: the dropped registration held every dollar
            # (e.g. NY-4 H2NY04244, $4.6M) while the replacement had none. Keep the verified
            # ID unless FEC receipts positively say another one is the live committee.
            fec_id = kept
            if fec is not None:
                picked, _ = pick_house_fec(sorted(set(member_fecs) | {kept}), state, district, fec)
                if picked and picked != kept:
                    fec_id = picked
                    flags.append(f"  {seat:>6} {name}: {kept} superseded by {picked} (more receipts)")
            if fec_id == kept:
                flags.append(f"  {seat:>6} {name}: upstream dropped {kept}; kept it (same member)")
        else:
            fec_id, flag = pick_house_fec(member_fecs, state, district, fec)
            if kept and not same_member:
                flags.append(f"  {seat:>6} {name}: seat changed occupant "
                             f"({kept_bioguide} → {bioguide}) — dropped old {kept}, using {fec_id}")
            elif flag == "no-house-fec-id":
                flags.append(f"  {seat:>6} {name}: NO fec_id — finance will be skipped")
            elif flag:
                flags.append(f"  {seat:>6} {name}: {flag} → {fec_id}")

        roster.append({
            "name": name,
            "state": state,
            "district": district,
            "fec_id": fec_id,
            # Authoritative bioguide — s1 uses this instead of the unreliable district lookup.
            "bioguide_id": L["id"]["bioguide"],
        })

    roster.sort(key=lambda c: (c["state"], c["district"]))

    missing = [c for c in roster if not c["fec_id"]]
    print(f"\n  {len(roster)} voting House members "
          f"({len(roster) - len(existing)} new, {len(existing)} kept from existing roster)")
    print(f"  {len(missing)} without an fec_id")
    if flags:
        print(f"\n  Flags ({len(flags)} — eyeball these):")
        print("\n".join(flags))

    if args.dry_run:
        print("\n  [dry-run] not writing.")
        return

    with open(OUT, "w") as fh:
        json.dump(roster, fh, indent=2)
    print(f"\n  Wrote {len(roster)} → {os.path.relpath(OUT, REPO)}")


if __name__ == "__main__":
    main()
