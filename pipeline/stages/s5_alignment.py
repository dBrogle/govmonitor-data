"""Stage 5: Aggregate all pipeline data into a single file per candidate.

Combines member profile, finance data, and alignment scores with per-topic
bill breakdowns showing which votes contributed to each topic alignment.
"""

import json
from pathlib import Path

from services.congress.topics import TOPICS, TOPICS_BY_SLUG
from services.congress.vote_questions import classify_vote_question, vote_weight
from utils.cache import CACHE_DIR

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s5_alignment"
MEMBERS_DIR = Path(__file__).parent.parent / "output" / "s1_members"
FINANCE_DIR = Path(__file__).parent.parent / "output" / "s2_finance"
BILLS_DIR = Path(__file__).parent.parent / "output" / "s3_bills"
ANALYSIS_DIR = Path(__file__).parent.parent / "output" / "s4_analysis"
STANCES_DIR = Path(__file__).parent.parent / "output" / "s6_stances"
BIPARTISAN_DIR = Path(__file__).parent.parent / "output" / "s7_bipartisanship"
VOTE_MEMBERS_CACHE = CACHE_DIR / "vote_members"

CONGRESS_URL = "https://www.congress.gov/bill/{congress}th-congress/{path}/{number}"

# Confidence thresholds: how many signals (votes + sponsorship) back a topic's alignment.
# A topic moved by a single signal is far less reliable than one backed by many.
CONFIDENCE_MEDIUM_MIN = 3
CONFIDENCE_HIGH_MIN = 8

# Stance-signal weights by role. A floor-passage vote is the formal recorded act (weight 1.0,
# via vote_weight()). Sponsoring is authoring a bill — a strong endorsement of its direction;
# cosponsoring is backing someone else's bill — weaker support. Sponsorship is always an
# endorsement (+); there is no "oppose by sponsorship". Tunable.
WEIGHT_SPONSOR = 0.8
WEIGHT_COSPONSOR = 0.4

# Evidence shrinkage: alignment = numerator / (evidence_mass + K_SHRINKAGE). Pulls thin-evidence
# topics toward neutral so a member who barely engages a topic can't show a full-strength bar;
# topics with lots of evidence are ~unaffected. K ≈ "one solid sponsored bill" of evidence.
# Tunable — raise for a more conservative (more-evidence-required) scale.
K_SHRINKAGE = 1.0

# Retired topic slugs folded into a live one. Currently EMPTY on purpose: `government_spending`
# and `national_debt` were replaced by `budget_deficit`, and their cached scores are deliberately
# NOT aliased forward — the model reasoned about spending *levels* on those axes, so reusing the
# numbers under a deficit label would claim a judgement it never made. Those scores are simply
# ignored (unknown slug) and the bills were re-scored on the new axis instead. Add an entry here
# only when a rename genuinely preserves the meaning of the axis.
TOPIC_ALIASES: dict[str, str] = {}


def _load_vote_questions() -> dict[tuple[int, int], str]:
    """Map (session, vote_number) → substantive voteQuestion from the cached vote headers.

    The substantive question ("On Passage" / "On Motion to Recommit" / "On Agreeing to the
    Amendment") lives only in the house-vote /members endpoint header — not in the list
    endpoint that feeds voting_history (which carries only voteType, the voting *method*).
    We read it from the request cache, the authoritative copy that's always present once a
    vote's member positions were fetched. Vote numbers reset per session, so the key includes
    the session."""
    out: dict[tuple[int, int], str] = {}
    if not VOTE_MEMBERS_CACHE.exists():
        return out
    for f in VOTE_MEMBERS_CACHE.glob("*_members.json"):
        try:
            hdr = json.loads(f.read_text()).get("houseRollCallVoteMemberVotes", {})
        except (ValueError, OSError):
            continue
        session, vote_number, q = hdr.get("sessionNumber"), hdr.get("rollCallNumber"), hdr.get("voteQuestion")
        if session is not None and vote_number is not None and q:
            out[(int(session), int(vote_number))] = q
    return out


def _session_for_date(vote_date: str | None) -> int | None:
    """119th Congress session from a vote date: session 1 = 2025, session 2 = 2026.

    Fallback only. s1_members now records `session` on every vote; this covers member
    files written before that field existed. Extend the map if you resurrect one for
    another congress.
    """
    if not vote_date:
        return None
    return {"2025": 1, "2026": 2}.get(vote_date[:4])


def _confidence(n: int) -> str:
    if n >= CONFIDENCE_HIGH_MIN:
        return "high"
    if n >= CONFIDENCE_MEDIUM_MIN:
        return "medium"
    return "low"


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

    # Substantive vote question per roll call (shared across all candidates; load once).
    vote_questions = _load_vote_questions()
    print(f"  Loaded substantive question for {len(vote_questions)} roll-call votes")

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

        # ── Gather weighted contributions (votes + sponsorship) ──────────
        # A member's stance on a topic is built from several weighted signals, not votes
        # alone. Each bill the member engaged contributes once, at the weight of their
        # strongest role for it (vote > sponsor > cosponsor):
        #   • Vote on final passage — formal, directional (Yea/+1, Nay/−1). Procedural/amendment
        #     votes carry no weight and are excluded (see vote_questions.py).
        #   • Sponsored — authored the bill: a strong endorsement of its direction (+1).
        #   • Cosponsored — backed someone else's bill: weaker support (+1).
        # Sponsorship has no "oppose" — you either back a bill or you don't. When a member both
        # voted on and sponsored the same bill, the vote (formal + directional) takes precedence.
        sponsored = [
            b for b in member_data.get("sponsored_bills", [])
            # Drop placeholder bills leadership reserves at the start of a Congress.
            if "reserved for the speaker" not in (b.get("title") or "").lower()
        ]
        cosponsored = member_data.get("cosponsored_bills", [])

        contributions: dict[tuple[str, str], dict] = {}  # (TYPE, number) → contribution
        votes_excluded = 0  # directional votes dropped because the question is procedural/amendment

        for vote in votes:
            bill_ref = vote.get("bill")
            if not bill_ref:
                continue
            bt = bill_ref.get("type", "").upper()
            bn = str(bill_ref.get("number", ""))
            if not bt or not bn:
                continue
            position = vote.get("member_position", "")
            # Yea/Nay (final passage) and Aye/No (Committee of the Whole) are both directional —
            # recognizing both is what fixed the Barry-Moore "100% pro-LGBT" sign-flip bug.
            yea = position in ("Yea", "Aye")
            nay = position in ("Nay", "No")
            session = vote.get("session") or _session_for_date(vote.get("vote_date"))
            vnum = vote.get("vote_number")
            question = vote_questions.get((session, int(vnum))) if session and vnum is not None else None
            weight = vote_weight(question)
            if (yea or nay) and weight == 0:
                votes_excluded += 1
            if not ((yea or nay) and weight > 0):
                continue  # procedural / amendment / non-directional → no stance signal
            contributions[(bt, bn)] = {
                "congress": congress, "weight": weight, "sign": 1 if yea else -1,
                "role": "vote", "vote_position": position,
                "vote_class": classify_vote_question(question), "vote_date": vote.get("vote_date"),
            }

        for b in sponsored:
            key = (b["type"].upper(), str(b["number"]))
            if key in contributions:  # a vote on the same bill outranks sponsorship
                continue
            contributions[key] = {"congress": b.get("congress", congress),
                                  "weight": WEIGHT_SPONSOR, "sign": 1, "role": "sponsor"}
        for b in cosponsored:
            key = (b["type"].upper(), str(b["number"]))
            if key in contributions:
                continue
            contributions[key] = {"congress": b.get("congress", congress),
                                  "weight": WEIGHT_COSPONSOR, "sign": 1, "role": "cosponsor"}

        # ── Aggregate into per-topic alignment ───────────────────────────
        topic_numerator: dict[str, float] = {}
        topic_denominator: dict[str, float] = {}
        topic_bills: dict[str, list[dict]] = {}      # slug → contributing-bill list
        topic_signal_count: dict[str, int] = {}      # slug → # of signals (for confidence)

        bills_scored = 0
        bills_skipped = 0

        for (bt, bn), c in contributions.items():
            analysis = _load_json(ANALYSIS_DIR / f"{c['congress']}_{bt}_{bn}.json")
            if not analysis:
                bills_skipped += 1
                continue
            bills_scored += 1
            w, sign = c["weight"], c["sign"]

            for score_item in analysis.get("scores", []):
                slug = TOPIC_ALIASES.get(score_item["topic_slug"], score_item["topic_slug"])
                score = score_item["score"]
                # Skip 0s and topics since removed from topics.py (cached files may carry them).
                if score == 0 or slug not in TOPICS_BY_SLUG:
                    continue

                # Endorsing the bill's direction (+sign) adds sign·score to the numerator;
                # every signal adds weight·|score| of "stake" to the denominator.
                topic_denominator[slug] = topic_denominator.get(slug, 0) + w * abs(score)
                topic_numerator[slug] = topic_numerator.get(slug, 0) + w * sign * score
                topic_signal_count[slug] = topic_signal_count.get(slug, 0) + 1

                topic_bills.setdefault(slug, []).append({
                    "bill_type": bt,
                    "bill_number": bn,
                    "role": c["role"],                    # vote | sponsor | cosponsor
                    "weight": w,
                    "vote_position": c.get("vote_position"),  # None for sponsorship
                    "vote_class": c.get("vote_class"),
                    "bill_topic_score": round(score, 4),
                    "contributed_to_alignment": True,
                    "vote_date": c.get("vote_date"),
                    # title/url/summary fetched lazily by bill id; url derived client-side.
                })

        n_vote = sum(1 for c in contributions.values() if c["role"] == "vote")
        n_spon = sum(1 for c in contributions.values() if c["role"] == "sponsor")
        n_cospon = sum(1 for c in contributions.values() if c["role"] == "cosponsor")
        print(f"    {len(contributions)} contributions ({n_vote} votes, {n_spon} sponsored, "
              f"{n_cospon} cosponsored) — {bills_scored} scored, {bills_skipped} without analysis, "
              f"{votes_excluded} procedural/amendment votes excluded")

        # ── Assemble alignment topics ────────────────────────────────────
        # `denominator` is the evidence mass M (Σ weight·|score|). Salience = a topic's share
        # of the member's total evidence across all topics — how much of their legislative
        # attention sits there. Used to order/emphasize topics, separate from direction.
        total_mass = sum(topic_denominator.values())

        alignments = []
        for slug, denom in topic_denominator.items():
            numer = topic_numerator.get(slug, 0)
            # Direction with evidence shrinkage toward neutral: thin topics are pulled to 0,
            # well-evidenced topics keep their raw lean. raw_alignment kept for transparency.
            raw_alignment = numer / denom if denom else 0
            alignment = numer / (denom + K_SHRINKAGE)
            salience = denom / total_mass if total_mass else 0
            topic_cfg = TOPICS_BY_SLUG.get(slug)
            topic_name = topic_cfg.name if topic_cfg else slug

            # Sort bills by absolute impact descending
            bills_for_topic = sorted(
                topic_bills.get(slug, []),
                key=lambda b: abs(b["bill_topic_score"]),
                reverse=True,
            )

            n_signals = topic_signal_count.get(slug, 0)
            alignments.append({
                "topic_slug": slug,
                "topic_name": topic_name,
                # Shrunk, engagement-aware lean (the displayed bar); raw lean kept alongside.
                "alignment": round(alignment, 4),
                "raw_alignment": round(raw_alignment, 4),
                "numerator": round(numer, 4),
                "denominator": round(denom, 4),
                # Share of the member's total evidence on this topic — drives prominence/order.
                "salience": round(salience, 4),
                # How many signals (votes + sponsorship) back this score — a 1-signal topic is
                # far less reliable than a many-signal one. `confidence` is a display bucket.
                "contributing_signal_count": n_signals,
                "confidence": _confidence(n_signals),
                "minus_one_desc": topic_cfg.minus_one_desc if topic_cfg else None,
                "plus_one_desc": topic_cfg.plus_one_desc if topic_cfg else None,
                "contributing_bills": bills_for_topic,
            })

        # Order by the shrunk lean magnitude: the strongest, best-evidenced stances first.
        # (Shrinkage already mutes thin topics, so a high |alignment| means both a clear lean
        # AND real engagement — better than pure salience, which a high-volume catch-all topic
        # like government_spending would dominate for nearly everyone.)
        alignments.sort(key=lambda x: abs(x["alignment"]), reverse=True)

        # ── Attach stated positions + compute the truth score ────────────
        # Pair each topic's vote-based lean with the member's STATED stance (from s6_stances):
        # two comparable −1..+1 scores (the double bars). The truth score is their agreement,
        # over topics where the member both took a clear public position AND has enough votes to
        # trust the lean — the confidence gate keeps a protest vote or thin record from reading
        # as a "lie".
        stances_data = _load_json(STANCES_DIR / f"{state}_{district}.json")
        stated_by_slug = {
            s["topic_slug"]: s
            for s in (stances_data or {}).get("stances", [])
            if s.get("addressed") and s.get("stated_score") is not None
        }

        # Weight each comparable topic by how much the member PREACHES it (stated emphasis), so
        # the overall score reflects consistency on their signature issues, not a topic they
        # mentioned once. Emphasis floored so an addressed-but-low-emphasis topic still counts a
        # little; falls back to a flat mean if no emphasis is recorded.
        truth_pairs = []  # (consistency in [0,1], weight) over comparable, confident topics
        for a in alignments:
            st = stated_by_slug.get(a["topic_slug"])
            if not st:
                a["stated"] = None
                continue
            emph = st.get("emphasis")
            emph = emph if isinstance(emph, (int, float)) else None
            a["stated"] = {
                "score": round(st["stated_score"], 4),
                "emphasis": round(emph, 4) if emph is not None else None,
                "quote": st.get("quote"),
                "reasoning": st.get("reasoning"),
                "source": (stances_data or {}).get("website"),
            }
            if a["confidence"] in ("medium", "high"):
                gap = abs(st["stated_score"] - a["alignment"])  # 0..2 on the shared axis
                consistency = 1 - gap / 2                        # 1 = identical, 0 = opposite
                weight = max(emph if emph is not None else 0.5, 0.05)
                truth_pairs.append((consistency, weight))

        total_w = sum(w for _, w in truth_pairs)
        truth_score = {
            "score": round(sum(c * w for c, w in truth_pairs) / total_w * 100) if total_w else None,
            "topics_compared": len(truth_pairs),
            "fetched_at": (stances_data or {}).get("fetched_at"),
            "note": (
                "How consistently the member votes with what they publicly say, weighted by how "
                "much they emphasize each topic on their site (100 = words match votes exactly). "
                "Only high-confidence topics count; null if too few are comparable."
            ),
        }

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
                "truth_score": truth_score,
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
                # Most recent first. Roll call numbers rise strictly with time inside a
                # session and reset between them, so (session, vote_number) orders exactly
                # — and unlike vote_date it never ties.
                for v in sorted(
                    votes,
                    key=lambda v: (
                        v.get("session") or _session_for_date(v.get("vote_date")) or 0,
                        v.get("vote_number") or 0,
                    ),
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
            # Cross-party voting/cosponsorship rates (s7). Its own block, not an alignment
            # topic: it's a statistical rate, not an LLM-scored left/right axis, and it has no
            # stated-stance counterpart so it never feeds the truth score.
            "bipartisanship": _load_json(BIPARTISAN_DIR / f"{state}_{district}.json"),
            "finance": finance_data,
        }

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"  [done] {name} → {out.relative_to(OUTPUT_DIR.parent.parent)}")
