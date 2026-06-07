"""
Test script: pull campaign funding data from OpenFEC for 3 congress members.
Run from the data/ directory:  python scripts/fetch_fec.py

Requires OPEN_FEC_API_KEY in a .env file (or env var).
Get a free key at: https://api.data.gov/signup/
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from services.campaign_finance.fec import FECService

load_dotenv(Path(__file__).parent.parent / ".env")

CANDIDATES = [
    ("Alexandria Ocasio-Cortez", "H8NY15148"),  # D - NY-14
    ("Marjorie Taylor Greene",   "H0GA14050"),  # R - GA-14
    ("Nancy Pelosi",             "H8CA05035"),  # D - CA-11
]

CYCLE = 2024

W = 72  # total display width


def header(title):
    print(f"\n{'─' * W}")
    print(f"  {title}")
    print(f"{'─' * W}")


def section(title):
    print(f"\n  ┌─ {title}")


def row(label, value, indent=4):
    print(f"  {'│':<{indent-2}} {label:<28} {value}")


def money(amount):
    return f"${amount:>14,.2f}"


def main():
    api_key = os.getenv("OPEN_FEC_API_KEY", "DEMO_KEY")
    svc = FECService(api_key=api_key)

    print(f"\n{'═' * W}")
    print(f"  OpenFEC Funding Snapshot — {CYCLE} Cycle")
    print(f"{'═' * W}")

    for name, candidate_id in CANDIDATES:
        header(f"{name}  ({candidate_id})")

        # ── Totals ──────────────────────────────────────────────────────────
        totals = svc.get_candidate_totals(candidate_id, CYCLE)
        section("Fundraising totals")
        if totals:
            row("Total raised",        money(totals.receipts))
            row("Total spent",         money(totals.disbursements))
            row("Cash on hand",        money(totals.cash_on_hand_end_period))
            row("Individual contribs", money(totals.individual_itemized_contributions))
            row("PAC money (in)",      money(totals.other_political_committee_contributions))
        else:
            print("  │  (no totals for this cycle)")

        # ── Committee structure ──────────────────────────────────────────────
        # Money flows: donors → joint fundraising committees (JFCs) → principal
        # We need to look at JFC schedule_a entries to find real external donors.
        committees = svc.get_committees(candidate_id, CYCLE)
        principal = next((c for c in committees if c.designation == "P"), None)
        jfcs = [c for c in committees if c.designation == "J"]

        # ── Top external PAC contributions (across JFCs) ─────────────────────
        section("Top PAC contributions (across fundraising committees)")
        try:
            # Collect non-individual contributions from principal + all JFCs,
            # skip contributors that are one of the candidate's own committees.
            # Aggregate by contributor_id to avoid showing multiple transactions.
            own_ids = {c.committee_id for c in committees}
            totals_by_pac: dict[str, float] = {}
            name_by_pac:   dict[str, str]   = {}
            for comm in ([principal] if principal else []) + jfcs:
                raw = svc.get_contributions(comm.committee_id, CYCLE, individuals_only=False, limit=20)
                for c in raw:
                    if c.contributor_id and c.contributor_id not in own_ids:
                        totals_by_pac[c.contributor_id] = totals_by_pac.get(c.contributor_id, 0) + c.amount
                        name_by_pac[c.contributor_id] = c.contributor_name or c.contributor_id

            if totals_by_pac:
                ranked = sorted(totals_by_pac.items(), key=lambda x: x[1], reverse=True)
                for pac_id, total in ranked[:5]:
                    detail   = svc.get_committee_detail(pac_id)
                    org      = (detail.connected_organization_name or "—") if detail else "—"
                    org_type = (detail.organization_type_full or "—")       if detail else "—"
                    print(f"  │  {money(total)}  {name_by_pac[pac_id]}")
                    print(f"  │               org: {org}  [{org_type}]")
            else:
                print("  │  (no external PAC contributions found)")
        except Exception as e:
            print(f"  │  (error: {e})")

        # ── Individual contributions (from JFCs where real donors land) ────────
        section("Top individual contributions (across fundraising committees)")
        try:
            totals_by_person: dict[str, float] = {}
            meta_by_person:   dict[str, tuple] = {}  # name → (occupation, employer)
            for comm in jfcs or ([principal] if principal else []):
                raw = svc.get_contributions(comm.committee_id, CYCLE, individuals_only=True, limit=20)
                for c in raw:
                    key = c.contributor_name or "unknown"
                    totals_by_person[key] = totals_by_person.get(key, 0) + c.amount
                    meta_by_person.setdefault(key, (c.contributor_occupation, c.contributor_employer))
            ranked = sorted(totals_by_person.items(), key=lambda x: x[1], reverse=True)
            for person, total in ranked[:5]:
                occ, emp = meta_by_person[person]
                print(f"  │  {money(total)}  {person}")
                print(f"  │               {occ or '—'} @ {emp or '—'}")
        except Exception as e:
            print(f"  │  (error: {e})")

        # ── Top employers (from JFCs) ─────────────────────────────────────────
        section("Top contributing employers (across fundraising committees)")
        try:
            for comm in jfcs or ([principal] if principal else []):
                by_employer = svc.get_contributions_by_employer(comm.committee_id, CYCLE, limit=5)
                if by_employer:
                    print(f"  │  via {comm.name}:")
                    for e in by_employer:
                        print(f"  │    {money(e.total)}  {e.employer or '(unknown)'}  ({e.count} donors)")
        except Exception as e:
            print(f"  │  (error: {e})")

        # ── Independent expenditures ─────────────────────────────────────────
        section("Independent expenditures (PAC spending for/against)")
        try:
            indep = svc.get_independent_expenditures(candidate_id, CYCLE)
            if indep:
                for ie in indep:
                    direction = "FOR    " if ie.support_oppose_indicator == "S" else "AGAINST"
                    detail    = svc.get_committee_detail(ie.committee_id) if ie.committee_id else None
                    pac_type  = (detail.committee_type_full or "—") if detail else "—"
                    org       = (detail.connected_organization_name or "—") if detail else "—"
                    print(f"  │  {money(ie.total)}  {direction}  {ie.committee_name or '—'}")
                    print(f"  │               type: {pac_type}  org: {org}")
            else:
                print("  │  (none)")
        except Exception as e:
            print(f"  │  (error: {e})")

        # ── Electioneering ───────────────────────────────────────────────────
        section("Electioneering broadcast spend")
        try:
            elex = svc.get_electioneering(candidate_id, CYCLE)
            if elex:
                for el in elex:
                    print(f"  │  {money(el.total)}  {el.committee_name or '—'}  ({el.count} filings)")
            else:
                print("  │  (none)")
        except Exception as e:
            print(f"  │  (error: {e})")

        print()


if __name__ == "__main__":
    main()
