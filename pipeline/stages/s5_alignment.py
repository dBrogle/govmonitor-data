"""Stage 5: Aggregate all pipeline data into a single file per candidate.

Combines member profile, finance data, and alignment scores with per-topic
bill breakdowns showing which votes contributed to each topic alignment.
"""

import json
from pathlib import Path

from services.congress.topics import TOPICS, TOPICS_BY_SLUG

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s5_alignment"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"
FINANCE_DIR = Path(__file__).parent.parent / "output" / "s2_finance"
BILLS_DIR = Path(__file__).parent.parent / "output" / "s3_bills"
ANALYSIS_DIR = Path(__file__).parent.parent / "output" / "s4_analysis"

CONGRESS_URL = "https://www.congress.gov/bill/{congress}th-congress/{path}/{number}"


def output_path(state: str, district: int) -> Path:
    return OUTPUT_DIR / f"{state}_{district}.json"


def _load_json(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _bill_url(congress: int, bill_type: str, bill_number: str) -> str:
    """Build a congress.gov URL for a bill.

    The path segment is the full congress.gov slug — only bills (HR/S) use the "-bill"
    suffix; resolutions do not (e.g. HRES 189 → "house-resolution/189").
    """
    path_map = {
        "hr": "house-bill",
        "hres": "house-resolution",
        "hjres": "house-joint-resolution",
        "hconres": "house-concurrent-resolution",
        "s": "senate-bill",
        "sres": "senate-resolution",
        "sjres": "senate-joint-resolution",
        "sconres": "senate-concurrent-resolution",
    }
    path = path_map.get(bill_type.lower(), "house-bill")
    return CONGRESS_URL.format(congress=congress, path=path, number=bill_number)


def _slim_bill_ref(b: dict) -> dict:
    """Minimal sponsored/cosponsored bill reference for the candidate payload.

    Keeps only the bill id + introduced date; title/summary/topic_scores are fetched
    lazily by id, the congress.gov url is derived client-side, and latest_action is
    dropped (not rendered by the bill card)."""
    return {
        "congress": b["congress"],
        "number": b["number"],
        "type": b["type"],
        "introduced_date": b.get("introduced_date"),
    }


def run(candidates: list[dict], config: dict, *, force: bool = False):
    congress = config["congress"]

    if not MEMBERS_DIR.exists():
        print("  [!] No members output found — run the 'members' stage first")
        return

    for candidate in candidates:
        state = candidate["state"]
        district = candidate["district"]
        name = candidate["name"]
        fec_id = candidate.get("fec_id")
        out = output_path(state, district)

        if out.exists() and not force:
            print(f"  [skip] {name} ({state}-{district}) — already processed")
            continue

        # ── Load source data ─────────────────────────────────────────────
        member_data = _load_json(MEMBERS_DIR / f"{state}_{district}.json")
        if not member_data:
            print(f"  [skip] {name} — no member data (run 'members' stage first)")
            continue

        finance_data = _load_json(FINANCE_DIR / f"{fec_id}.json") if fec_id else None
        votes = member_data.get("voting_history", [])

        print(f"\n  Processing {name} ({state}-{district})...")

        # ── Compute alignment with per-topic bill breakdowns ─────────────
        # Per topic, track: numerator, denominator, and contributing bills
        topic_numerator: dict[str, float] = {}
        topic_denominator: dict[str, float] = {}
        topic_bills: dict[str, list[dict]] = {}  # slug → list of bill contributions

        bills_scored = 0
        bills_skipped = 0

        for vote in votes:
            bill_ref = vote.get("bill")
            if not bill_ref:
                continue

            bill_type = bill_ref.get("type", "").upper()
            bill_number = str(bill_ref.get("number", ""))
            if not bill_type or not bill_number:
                continue

            analysis = _load_json(ANALYSIS_DIR / f"{congress}_{bill_type}_{bill_number}.json")
            if not analysis:
                bills_skipped += 1
                continue

            bills_scored += 1
            position = vote.get("member_position", "")

            # Only votes that express a for/against stance move the score.
            # Present / Not Voting are deliberate non-positions and are excluded
            # from both numerator and denominator (an abstention isn't a signal).
            yea = position == "Yea"
            nay = position == "Nay"
            counts = yea or nay

            for score_item in analysis.get("scores", []):
                slug = score_item["topic_slug"]
                score = score_item["score"]

                # Skip topics no longer in the config — cached analysis files may
                # still carry a topic that was since removed from topics.py.
                if score == 0 or slug not in TOPICS_BY_SLUG:
                    continue

                if counts:
                    # A Yea endorses the bill's direction (+score); a Nay opposes
                    # it (-score). Both add |score| of "stake" to the denominator.
                    topic_denominator[slug] = topic_denominator.get(slug, 0) + abs(score)
                    topic_numerator[slug] = topic_numerator.get(slug, 0) + (score if yea else -score)

                # Record this bill's contribution to the topic
                if slug not in topic_bills:
                    topic_bills[slug] = []

                topic_bills[slug].append({
                    "bill_type": bill_type.upper(),
                    "bill_number": bill_number,
                    "vote_position": position,
                    "bill_topic_score": round(score, 4),
                    "contributed_to_alignment": counts,
                    "vote_date": vote.get("vote_date"),
                    # title/url/summary omitted — title+summary fetched lazily by id,
                    # url derived client-side. (Inlined titles were duplicated across
                    # every topic a bill touched — ~20KB per candidate.)
                })

        print(f"    {bills_scored} bills with analysis, {bills_skipped} without")

        # ── Assemble alignment topics ────────────────────────────────────
        alignments = []
        for slug, denom in topic_denominator.items():
            numer = topic_numerator.get(slug, 0)
            alignment = numer / denom if denom else 0
            topic_cfg = TOPICS_BY_SLUG.get(slug)
            topic_name = topic_cfg.name if topic_cfg else slug

            # Sort bills by absolute impact descending
            bills_for_topic = sorted(
                topic_bills.get(slug, []),
                key=lambda b: abs(b["bill_topic_score"]),
                reverse=True,
            )

            alignments.append({
                "topic_slug": slug,
                "topic_name": topic_name,
                "alignment": round(alignment, 4),
                "numerator": round(numer, 4),
                "denominator": round(denom, 4),
                "minus_one_desc": topic_cfg.minus_one_desc if topic_cfg else None,
                "plus_one_desc": topic_cfg.plus_one_desc if topic_cfg else None,
                "contributing_bills": bills_for_topic,
            })

        alignments.sort(key=lambda x: abs(x["alignment"]), reverse=True)

        topics_with_signal = {a["topic_slug"] for a in alignments}
        silent_topics = [
            {"topic_slug": t.slug, "topic_name": t.name}
            for t in TOPICS if t.slug not in topics_with_signal
        ]

        # ── Assemble final aggregated output ─────────────────────────────
        member_detail = member_data.get("member_detail", {})

        result = {
            "profile": {
                "bioguide_id": member_data["bioguide_id"],
                "name": name,
                "state": state,
                "district": district,
                "party": member_data.get("party"),
                "birth_year": member_detail.get("birth_year"),
                "website": member_detail.get("official_website_url"),
                "serving_since": (member_detail.get("party_history") or [{}])[0].get("startYear"),
                "terms": member_detail.get("terms", []),
                "fec_id": fec_id,
            },
            "alignment": {
                "congress": congress,
                "votes_analyzed": bills_scored,
                "votes_without_analysis": bills_skipped,
                "topics": alignments,
                "topics_without_signal": silent_topics,
            },
            "voting_history": [
                {
                    **v,
                    # Keep only the bill id (type+number); title fetched lazily by id,
                    # url derived client-side.
                    "bill": {
                        "number": v["bill"]["number"],
                        "type": v["bill"]["type"],
                    } if v.get("bill") else None,
                }
                # Most recent first; vote_number breaks ties within a day.
                for v in sorted(
                    votes,
                    key=lambda v: (v.get("vote_date") or "", v.get("vote_number") or 0),
                    reverse=True,
                )
            ],
            # Only the bill id + introduced date ship inline; title/summary/topic_scores
            # are fetched lazily by id on expand, url is derived client-side, and
            # latest_action (the new card doesn't render it) is dropped entirely.
            "sponsored_bills": [
                _slim_bill_ref(b)
                # Drop placeholder bills leadership reserves at the start of a Congress
                # (low numbers titled "Reserved for the Speaker." with no real content).
                for b in member_data.get("sponsored_bills", [])
                if "reserved for the speaker" not in (b.get("title") or "").lower()
            ],
            "cosponsored_bills": [
                _slim_bill_ref(b)
                for b in member_data.get("cosponsored_bills", [])
            ],
            "finance": finance_data,
        }

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"  [done] {name} → {out.relative_to(OUTPUT_DIR.parent.parent)}")
