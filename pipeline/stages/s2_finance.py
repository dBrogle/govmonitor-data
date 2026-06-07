"""Stage 2: Fetch campaign finance data from OpenFEC."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from services.campaign_finance.fec import FECService
from services.campaign_finance.models import CommitteeTotals

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s2_finance"
PROFILE_DIR = OUTPUT_DIR / "pac_profiles"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"

# schedule_a/by_size band lower bounds → human labels.
SIZE_LABELS = {
    0: "Under $200",
    200: "$200–$499",
    500: "$500–$999",
    1000: "$1,000–$1,999",
    2000: "$2,000+",
}

# Components of `receipts`, in the order they should appear in the funding breakdown.
# Each entry maps a display bucket to the CommitteeTotals field(s) that feed it. Together
# these account for the entire receipts figure — no unexplained "Other" wedge.
BREAKDOWN_BUCKETS: list[tuple[str, str, list[str]]] = [
    ("individual_small", "Small-dollar individuals", ["individual_unitemized_contributions"]),
    ("individual_large", "Large individuals (itemized)", ["individual_itemized_contributions"]),
    ("pac", "PACs & other committees", ["other_political_committee_contributions"]),
    ("party", "Party committees", ["political_party_committee_contributions"]),
    ("transfers", "Committee transfers", ["transfers_from_other_authorized_committee"]),
    ("self_funding", "Candidate self-funding", ["candidate_contribution"]),
    ("other", "Other receipts", ["offsets_to_operating_expenditures", "other_receipts"]),
]


def output_path(fec_id: str) -> Path:
    return OUTPUT_DIR / f"{fec_id}.json"


def _classify_lean(dem: float, rep: float, other: float, basis: str) -> dict:
    """Label a PAC's partisan lean from the party split of its recipients."""
    partisan = dem + rep
    if partisan <= 0:
        label = "Unclassified"
    else:
        dem_share = dem / partisan
        if dem == 0:
            label = "Republican"
        elif rep == 0:
            label = "Democratic"
        elif dem_share >= 0.8:
            label = "Leans Democratic"
        elif dem_share <= 0.2:
            label = "Leans Republican"
        else:
            label = "Bipartisan"
    return {
        "label": label,
        "dem_total": round(dem, 2),
        "rep_total": round(rep, 2),
        "other_total": round(other, 2),
        "basis": basis,
    }


def build_pac_profile(svc: FECService, committee_id: str, cycle: int, *, recipient_lookups: int = 8, force: bool = False) -> dict | None:
    """Build (or load cached) a standalone profile for a PAC: who it funds, who funds it,
    its size and partisan lean. Cached to its own file so it's shared across candidates and
    can back a future /pac/:id page. Returns None if the committee can't be resolved."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PROFILE_DIR / f"{committee_id}_{cycle}.json"
    if cache_file.exists() and not force:
        return json.loads(cache_file.read_text())

    detail = svc.get_committee_detail(committee_id)
    if not detail:
        return None
    totals = svc.get_committee_totals(committee_id, cycle)

    # Who it funds, and the partisan lean derived from those recipients.
    recipients = svc.get_disbursements_by_recipient(committee_id, cycle, limit=recipient_lookups)
    dem = rep = other = 0.0
    top_recipients = []
    for r in recipients:
        amount = r.total or 0.0
        party = None
        if r.recipient_id:
            try:
                rd = svc.get_committee_detail(r.recipient_id)
                party = rd.party if rd else None
            except Exception as e:
                print(f"    [warning] recipient party for {r.recipient_id}: {e}")
        if party == "DEM":
            dem += amount
        elif party == "REP":
            rep += amount
        else:
            other += amount
        top_recipients.append({
            "recipient_id": r.recipient_id,
            "recipient_name": r.recipient_name,
            "total": amount,
            "party": party,
        })

    # Who funds it, by contribution size.
    funding_by_size = []
    for b in svc.get_receipts_by_size(committee_id, cycle):
        if b.total and b.size is not None:
            funding_by_size.append({
                "label": SIZE_LABELS.get(b.size, f"${b.size}+"),
                "total": b.total,
                "count": b.count or 0,
            })

    profile = {
        "committee_id": committee_id,
        "name": detail.name,
        "committee_type_full": detail.committee_type_full,
        "organization_type_full": detail.organization_type_full,
        "connected_organization_name": detail.connected_organization_name,
        "cycle": cycle,
        "receipts": totals.receipts if totals else 0.0,
        "disbursements": totals.disbursements if totals else 0.0,
        "cash_on_hand": totals.last_cash_on_hand_end_period if totals else 0.0,
        "lean": _classify_lean(dem, rep, other, f"top {len(recipients)} recipients"),
        "top_recipients": top_recipients,
        "funding_by_size": funding_by_size,
    }
    cache_file.write_text(json.dumps(profile, indent=2, default=str))
    return profile


def build_funding_breakdown(ct: CommitteeTotals) -> dict:
    """Decompose receipts into labeled buckets that sum back to the total.

    `unaccounted` is the residual (receipts minus the sum of buckets); it should be
    near zero and is surfaced honestly rather than hidden in an "Other" catch-all.
    """
    components = []
    for key, label, fields in BREAKDOWN_BUCKETS:
        amount = sum(getattr(ct, f, 0.0) or 0.0 for f in fields)
        if amount:
            components.append({"key": key, "label": label, "amount": amount})
    components.sort(key=lambda c: c["amount"], reverse=True)
    accounted = sum(c["amount"] for c in components)
    return {
        "source": "committee_totals",
        "total": ct.receipts,
        "components": components,
        "unaccounted": round(ct.receipts - accounted, 2),
    }


DEFAULT_FINANCE_PARALLEL = 5


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


def _process_one(svc, candidate, *, cycle, contribution_limit,
                 pac_contribution_pages, individual_contribution_pages, force):
    fec_id = candidate.get("fec_id")
    name = candidate["name"]
    label = f"{name} ({fec_id})"

    if not fec_id:
        return name, "skip", None

    out = output_path(fec_id)
    if out.exists() and not force:
        return label, "skip", None

    print(f"\n  Processing {name} ({fec_id})...")
    # Guard each candidate so one exhausted-retry failure doesn't abort the batch.
    try:
        # Candidate totals
        totals = svc.get_candidate_totals(fec_id, cycle)

        # Committee structure
        committees = svc.get_committees(fec_id, cycle)
        principal = next((c for c in committees if c.designation == "P"), None)
        jfcs = [c for c in committees if c.designation == "J"]

        # Fallback: some candidates have no committee flagged "P" (e.g. MTG, whose campaign
        # money sits in a committee FEC labels designation "D"). Pick the committee with the
        # most receipts so the funding breakdown and transfer traversal still work.
        if not principal and committees:
            best_receipts = -1.0
            for c in committees:
                try:
                    t = svc.get_committee_totals(c.committee_id, cycle)
                    if t and t.receipts > best_receipts:
                        principal, best_receipts = c, t.receipts
                except Exception as e:
                    print(f"  [warning] totals for fallback committee {c.committee_id}: {e}")
            if principal:
                print(f"  [committee] no principal designation; using {principal.committee_id} (most receipts)")

        # Transfer sources (P5): committees that transferred funds into the principal —
        # joint fundraising committees and leadership funds. Their donors are the
        # candidate's real large contributors, hidden behind a single "transfer" line on
        # the principal. We pull individual donors from these too.
        transfer_sources: list[str] = []
        if principal:
            try:
                transfer_sources = svc.get_transfer_sources(principal.committee_id, cycle)
                if transfer_sources:
                    print(f"  [transfers] following {len(transfer_sources)} transfer source(s) into {principal.committee_id}")
            except Exception as e:
                print(f"  [warning] transfer sources: {e}")

        # Funding breakdown from the principal committee's totals. This endpoint exposes
        # individual_unitemized_contributions (small-dollar donors) and the other receipt
        # categories that candidates/totals/ omits, so the breakdown sums to receipts
        # instead of dumping the majority into an opaque "Other" bucket.
        funding_breakdown = None
        if principal:
            committee_totals = svc.get_committee_totals(principal.committee_id, cycle)
            if committee_totals:
                funding_breakdown = build_funding_breakdown(committee_totals)

        # Top PAC contributions (across committees)
        own_ids = {c.committee_id for c in committees}
        pac_totals: dict[str, float] = {}
        pac_names: dict[str, str] = {}
        pac_details: dict[str, dict] = {}

        for comm in committees:
            try:
                # Server-side filtered to line 11C, so every record is a real PAC receipt.
                # Aggregate per contributor since a PAC's cycle total is split across receipts.
                raw = svc.get_pac_contributions(comm.committee_id, cycle, max_pages=pac_contribution_pages)
                for c in raw:
                    if c.contributor_id and c.contributor_id not in own_ids:
                        pac_totals[c.contributor_id] = pac_totals.get(c.contributor_id, 0) + c.amount
                        pac_names[c.contributor_id] = c.contributor_name or c.contributor_id
            except Exception as e:
                print(f"  [warning] PAC contributions for {comm.committee_id}: {e}")

        # Get detail for top PACs
        ranked_pacs = sorted(pac_totals.items(), key=lambda x: x[1], reverse=True)[:10]
        for pac_id, _ in ranked_pacs:
            try:
                detail = svc.get_committee_detail(pac_id)
                if detail:
                    pac_details[pac_id] = detail.model_dump()
            except Exception as e:
                # A single committee-detail timeout shouldn't abort the whole run;
                # the PAC still shows with its contribution total, just without the
                # type/org enrichment.
                print(f"  [warning] committee detail for {pac_id}: {e}")

        # Source committees for individual donors: the principal (or fallback), plus the
        # JFC/leadership transfer sources discovered above (P5) — so donors who gave
        # through a joint committee surface instead of collapsing into a "transfer".
        base_committees = jfcs or ([principal] if principal else []) or committees
        individual_source_ids: list[str] = [c.committee_id for c in base_committees]
        for cid in transfer_sources:
            if cid not in individual_source_ids:
                individual_source_ids.append(cid)

        # Individual contributions — server-side filtered (line 11AI) and paginated, then
        # aggregated per donor across all source committees. This surfaces the real top
        # backers; the old top-N-receipts window summed to a tiny, unrepresentative slice
        # (a maxed-out donor gives repeatedly, so single receipts never reveal the total).
        individual_totals: dict[str, float] = {}
        individual_meta: dict[str, dict] = {}

        for cid in individual_source_ids:
            try:
                raw = svc.get_individual_contributions(cid, cycle, max_pages=individual_contribution_pages)
                for c in raw:
                    # Skip conduit/committee aggregates (e.g. WinRed/ActBlue) on the 11AI line.
                    if not c.is_individual:
                        continue
                    key = c.contributor_name or "unknown"
                    individual_totals[key] = individual_totals.get(key, 0) + c.amount
                    if key not in individual_meta:
                        individual_meta[key] = {
                            "occupation": c.contributor_occupation,
                            "employer": c.contributor_employer,
                            "state": c.contributor_state,
                        }
            except Exception as e:
                print(f"  [warning] individual contributions for {cid}: {e}")

        # Contributions by employer (same sources, so recovered donors' employers count too).
        # Aggregate across committees so an employer that appears under several isn't duplicated.
        employer_totals: dict[str, dict] = {}
        for cid in individual_source_ids:
            try:
                for e in svc.get_contributions_by_employer(cid, cycle, limit=10):
                    key = e.employer or "unknown"
                    agg = employer_totals.setdefault(key, {"employer": e.employer, "total": 0.0, "count": 0})
                    agg["total"] += e.total
                    agg["count"] += e.count
            except Exception as e:
                print(f"  [warning] employer contributions for {cid}: {e}")
        employer_data = sorted(employer_totals.values(), key=lambda x: x["total"], reverse=True)[:10]

        # Independent expenditures
        try:
            indep = svc.get_independent_expenditures(fec_id, cycle)
        except Exception as e:
            print(f"  [warning] independent expenditures: {e}")
            indep = []
        # Enrich top independent spenders with committee detail (guarded individually so a
        # single timeout doesn't discard the expenditure data we already fetched).
        for ie in indep[:10]:
            if ie.committee_id and ie.committee_id not in pac_details:
                try:
                    detail = svc.get_committee_detail(ie.committee_id)
                    if detail:
                        pac_details[ie.committee_id] = detail.model_dump()
                except Exception as e:
                    print(f"  [warning] committee detail for {ie.committee_id}: {e}")

        # Electioneering
        try:
            elex = svc.get_electioneering(fec_id, cycle)
        except Exception as e:
            print(f"  [warning] electioneering: {e}")
            elex = []

        # ── Data-quality checks ──────────────────────────────────────────
        # Surface silent gaps loudly. The most important: PAC dollars exist in the
        # totals but we captured no PAC contributors — the historical failure mode.
        warnings: list[str] = []
        pac_dollars = (totals.other_political_committee_contributions if totals else 0.0) or 0.0
        if pac_dollars > 0 and not ranked_pacs:
            warnings.append(
                f"totals report ${pac_dollars:,.0f} in PAC money but 0 PAC contributors were captured"
            )
        if funding_breakdown:
            receipts = funding_breakdown["total"] or 0.0
            resid = abs(funding_breakdown["unaccounted"])
            if receipts > 0 and resid > 0.01 * receipts:
                warnings.append(
                    f"funding breakdown leaves ${resid:,.0f} unaccounted "
                    f"({resid / receipts:.1%} of receipts)"
                )
        elif totals and (totals.receipts or 0) > 0:
            warnings.append("no committee totals — funding breakdown unavailable (principal committee missing?)")
        for w in warnings:
            print(f"  [data-quality] {name}: {w}")

        # ── PAC profiles ─────────────────────────────────────────────────
        # Profile the candidate's PAC donors and outside spenders (who each PAC funds,
        # who funds it, its size and partisan lean). Gated to candidates that will render
        # (member data present) to bound the first run; profiles are cached per committee
        # so shared PACs are built once and reused across candidates.
        pac_profiles: dict[str, dict] = {}
        renderable = (MEMBERS_DIR / f"{candidate['state']}_{candidate['district']}.json").exists()
        if renderable:
            profile_ids = [pac_id for pac_id, _ in ranked_pacs]
            profile_ids += [ie.committee_id for ie in indep[:10] if ie.committee_id]
            for cid in dict.fromkeys(profile_ids):  # dedupe, preserve order
                try:
                    prof = build_pac_profile(svc, cid, cycle, force=force)
                    if prof:
                        pac_profiles[cid] = prof
                except Exception as e:
                    print(f"  [warning] PAC profile for {cid}: {e}")
            print(f"  [profiles] built/loaded {len(pac_profiles)} PAC profiles")
        else:
            print(f"  [profiles] skipped (no member data yet for {name})")

        # Assemble output
        result = {
            "fec_id": fec_id,
            "name": name,
            "cycle": cycle,
            "fetched_at": datetime.now(timezone.utc).date().isoformat(),
            "data_quality_warnings": warnings,
            "totals": totals.model_dump() if totals else None,
            "funding_breakdown": funding_breakdown,
            "committees": [c.model_dump() for c in committees],
            "top_pac_contributions": [
                {
                    "contributor_id": pac_id,
                    "contributor_name": pac_names[pac_id],
                    "total": total,
                    "detail": pac_details.get(pac_id),
                }
                for pac_id, total in ranked_pacs
            ],
            "top_individual_contributions": sorted(
                [
                    {
                        "contributor_name": name_key,
                        "total": total,
                        **individual_meta.get(name_key, {}),
                    }
                    for name_key, total in individual_totals.items()
                ],
                key=lambda x: x["total"],
                reverse=True,
            )[:20],
            "contributions_by_employer": employer_data,
            "independent_expenditures": [ie.model_dump() for ie in indep],
            "electioneering": [el.model_dump() for el in elex],
            "committee_details": pac_details,
            "pac_profiles": pac_profiles,
        }

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"  [done] {name} → {out.relative_to(OUTPUT_DIR.parent.parent)}")
    except Exception as e:
        return label, "error", str(e)
    return label, "done", None


def run(candidates: list[dict], config: dict, *, force: bool = False):
    cycle = config["cycle"]
    contribution_limit = config.get("contribution_limit", 20)
    pac_contribution_pages = config.get("pac_contribution_pages", 5)
    individual_contribution_pages = config.get("individual_contribution_pages", 5)
    workers = config.get("finance_parallel", DEFAULT_FINANCE_PARALLEL)

    svc = FECService(api_key=config["_fec_api_key"])

    def fn(candidate):
        return _process_one(
            svc, candidate, cycle=cycle, contribution_limit=contribution_limit,
            pac_contribution_pages=pac_contribution_pages,
            individual_contribution_pages=individual_contribution_pages, force=force,
        )

    print(f"  Processing {len(candidates)} candidates' finance ({workers}-way parallel)...")
    _run_parallel(candidates, fn, workers=workers, label="finance")
