"""Stage 7: how much each member actually works across the aisle — purely statistical.

Deliberately NOT a topic. The alignment topics are LLM-scored −1..+1 left/right axes; this is
a different kind of measurement entirely (rates computed from the roll-call and sponsorship
record, no model in the loop), so it lives in its own block on the candidate payload alongside
finance rather than as another alignment bar.

Three independent signals, each a plain rate the member's own record can be checked against:

  1. vote_defection   — share of the member's directional, substantive votes cast AGAINST their
                        own party's majority on that roll call.
  2. cosponsor_reach  — share of the bills they COSPONSORED that were sponsored by the other party.
  3. attracted_reach  — share of the bills they SPONSORED that drew at least one cosponsor from
                        the other party (do they write bills the other side will sign?).

The composite is the mean of the three. Its absolute value means little (defection rates are
tiny; cosponsorship rates are not), so we also publish a percentile rank across the whole
House — that is the number worth showing, and it requires a cross-member pass, which is why
this stage scores every candidate together rather than one at a time.
"""

import json
import statistics
from pathlib import Path

from services.congress.vote_questions import vote_weight
from utils.cache import CACHE_DIR

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s7_bipartisanship"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"
BILLS_DIR = Path(__file__).parent.parent / "output" / "s3_bills"
VOTE_MEMBERS_CACHE = CACHE_DIR / "vote_members"

# Only D and R are treated as having a "party majority" to defect from. Independents have no
# caucus majority in this data, so their defection rate is left null rather than invented.
MAJOR_PARTIES = {"D", "R"}

# Below this many signals a rate is too thin to publish (one cosponsored bill from across the
# aisle is not a pattern). Mirrors the spirit of s5's confidence gate.
MIN_SIGNALS = 5

# Yea/Nay (final passage) and Aye/No (Committee of the Whole) are both directional.
YEA = {"Yea", "Aye"}
NAY = {"Nay", "No"}


def output_path(state: str, district: int) -> Path:
    return OUTPUT_DIR / f"{state}_{district}.json"


def _load_json(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return None
    return None


def _roll_call_party_majorities() -> dict[tuple[int, int], dict]:
    """(session, vote_number) → {"D": "Yea"|"Nay", "R": ..., "_weight": float}.

    Read from the cached /members roll-call payloads, the only place that carries every
    member's position AND party for a vote — the per-member voting_history has just the
    member's own position. `_weight` is the substantive-question weight (0 for procedural
    votes), so the caller can apply the same filter s5 uses.
    """
    out: dict[tuple[int, int], dict] = {}
    if not VOTE_MEMBERS_CACHE.exists():
        return out
    for f in VOTE_MEMBERS_CACHE.glob("*_members.json"):
        hdr = (_load_json(f) or {}).get("houseRollCallVoteMemberVotes") or {}
        session, number = hdr.get("sessionNumber"), hdr.get("rollCallNumber")
        if session is None or number is None:
            continue
        tally: dict[str, dict[str, int]] = {}
        for m in hdr.get("results") or []:
            party, cast = m.get("voteParty"), m.get("voteCast")
            if party not in MAJOR_PARTIES:
                continue
            side = "Yea" if cast in YEA else "Nay" if cast in NAY else None
            if side:
                tally.setdefault(party, {"Yea": 0, "Nay": 0})[side] += 1
        majority = {p: ("Yea" if c["Yea"] >= c["Nay"] else "Nay") for p, c in tally.items()}
        majority["_weight"] = vote_weight(hdr.get("voteQuestion"))
        out[(int(session), int(number))] = majority
    return out


def _sponsor_party(congress: int, bill_type: str, bill_number: str,
                   bills: dict[tuple, dict]) -> str | None:
    b = bills.get((congress, bill_type.upper(), str(bill_number)))
    sponsors = ((b or {}).get("detail") or {}).get("sponsors") or []
    return (sponsors[0].get("party") if sponsors else None)


def _cosponsor_parties(congress: int, bill_type: str, bill_number: str,
                       bills: dict[tuple, dict]) -> set[str]:
    b = bills.get((congress, bill_type.upper(), str(bill_number)))
    return {c.get("party") for c in ((b or {}).get("cosponsors") or [])}


def _rate(hits: int, total: int) -> float | None:
    """A rate, or None when there isn't enough evidence to publish one."""
    return round(hits / total, 4) if total >= MIN_SIGNALS else None


def run(candidates: list[dict], config: dict, *, force: bool = False):
    if not MEMBERS_DIR.exists():
        print("  [!] No members output found — run the 'members' stage first")
        return

    majorities = _roll_call_party_majorities()
    print(f"  Loaded party majorities for {len(majorities)} roll-call votes")

    # Bill sponsor/cosponsor parties, loaded once (10k+ files; per-member loading would re-read
    # the same popular bills hundreds of times).
    bills: dict[tuple, dict] = {}
    for f in BILLS_DIR.glob("*.json"):
        d = _load_json(f)
        if d:
            bills[(d["congress"], d["bill_type"].upper(), str(d["bill_number"]))] = d
    print(f"  Loaded sponsor/cosponsor parties for {len(bills)} bills")

    results: list[dict] = []
    for candidate in candidates:
        state, district, name = candidate["state"], candidate["district"], candidate["name"]
        member = _load_json(MEMBERS_DIR / f"{state}_{district}.json")
        if not member:
            print(f"  [skip] {name} — no member data")
            continue

        party = member.get("party")
        # Congress.gov spells the party out on the member record but abbreviates it on
        # sponsor/cosponsor records; normalise to the single letter used everywhere else.
        party = {"Democratic": "D", "Republican": "R", "Independent": "I"}.get(party, party)
        other = {"D": "R", "R": "D"}.get(party)

        # ── 1. Voting against your own party ─────────────────────────────
        votes_counted = defections = 0
        defection_examples: list[dict] = []
        if party in MAJOR_PARTIES:
            for v in member.get("voting_history", []):
                session, number = v.get("session"), v.get("vote_number")
                if session is None or number is None:
                    continue
                maj = majorities.get((int(session), int(number)))
                if not maj or not maj.get("_weight"):
                    continue  # unknown, or procedural/amendment — same filter s5 applies
                position = v.get("member_position")
                side = "Yea" if position in YEA else "Nay" if position in NAY else None
                own = maj.get(party)
                if side is None or own is None:
                    continue
                votes_counted += 1
                if side != own:
                    defections += 1
                    if len(defection_examples) < 10:
                        defection_examples.append({
                            "session": session, "vote_number": number,
                            "vote_date": v.get("vote_date"),
                            "bill": v.get("bill"),
                            "member_position": position,
                            "party_majority_position": own,
                        })

        # ── 2. Cosponsoring the other party's bills ──────────────────────
        cospon_counted = cospon_cross = 0
        for b in member.get("cosponsored_bills", []):
            sp = _sponsor_party(b.get("congress", config["congress"]), b["type"], b["number"], bills)
            if sp not in MAJOR_PARTIES:
                continue
            cospon_counted += 1
            if sp == other:
                cospon_cross += 1

        # ── 3. Your own bills attracting the other party ─────────────────
        spon_counted = spon_cross = 0
        for b in member.get("sponsored_bills", []):
            if "reserved for the speaker" in (b.get("title") or "").lower():
                continue
            key = (b.get("congress", config["congress"]), b["type"].upper(), str(b["number"]))
            if key not in bills:
                continue  # bill detail never fetched — can't tell, so don't count it either way
            spon_counted += 1
            if other in _cosponsor_parties(*key, bills):
                spon_cross += 1

        rates = {
            "vote_defection": _rate(defections, votes_counted),
            "cosponsor_reach": _rate(cospon_cross, cospon_counted),
            "attracted_reach": _rate(spon_cross, spon_counted),
        }
        present = [r for r in rates.values() if r is not None]

        results.append({
            "state": state, "district": district, "name": name,
            "bioguide_id": member.get("bioguide_id"), "party": party,
            **rates,
            # Equal-weighted mean of whichever signals cleared MIN_SIGNALS. Null when none did.
            "composite": round(statistics.mean(present), 4) if present else None,
            "signals": {
                "votes_counted": votes_counted, "defections": defections,
                "cosponsored_counted": cospon_counted, "cosponsored_cross_party": cospon_cross,
                "sponsored_counted": spon_counted, "sponsored_with_cross_party_support": spon_cross,
            },
            "defection_examples": defection_examples,
        })

    # ── Percentile rank across the House ─────────────────────────────────
    # The raw composite is uninterpretable on its own (is 0.19 a lot?). Rank makes it readable:
    # "more bipartisan than N% of the House". Ranked against everyone with a composite, both
    # parties together — the point is comparability, and a within-party rank would hide the
    # (real) fact that the parties defect at different rates.
    scored = sorted((r for r in results if r["composite"] is not None), key=lambda r: r["composite"])
    n = len(scored)
    for i, r in enumerate(scored):
        r["rank"] = n - i                       # 1 = most bipartisan
        # Share of the OTHER scored members this one ranks above, 0..100. Defined against
        # n-1 so the top member is 100 ("above every other member") and the bottom is 0.
        r["percentile"] = round(i / (n - 1) * 100) if n > 1 else None
    for r in results:
        r.setdefault("rank", None)
        r.setdefault("percentile", None)
        r["ranked_against"] = n
        r["note"] = (
            "Share of substantive votes cast against the member's own party majority, share of "
            "cosponsorships given to the other party's bills, and share of their own bills that "
            "drew cross-party cosponsors. Percentile is the share of the other "
            f"{n} scored House members this member's average ranks above."
        )
        # Surfaced so the UI never presents a capped rate as a complete one.
        r["caveats"] = [
            "Cosponsorship reach is measured over the most recent 50 cosponsorships on file "
            "(the Congress.gov per-member list is capped), not a member's full record.",
            "Cross-party support counts only sponsored bills whose cosponsor list was fetched.",
            "Procedural and amendment votes are excluded, matching the alignment scoring.",
        ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        output_path(r["state"], r["district"]).write_text(json.dumps(r, indent=2, default=str))

    if scored:
        med = statistics.median(r["composite"] for r in scored)
        print(f"  Scored {n}/{len(results)} members (median composite {med:.3f})")
        for label in ("most", "least"):
            picks = (scored[-3:][::-1] if label == "most" else scored[:3])
            print(f"    {label} bipartisan: " + ", ".join(
                f"{r['name']} ({r['party']}, {r['composite']:.3f})" for r in picks))
    else:
        print("  [!] No member had enough record to score")
