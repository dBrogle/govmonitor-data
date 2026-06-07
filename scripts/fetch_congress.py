"""
Test script: pull voting history, bill information, and bill content from
Congress.gov for House members.

Run from the data/ directory:
    python scripts/fetch_congress.py

Requires CONGRESS_API_KEY in a .env file (or env var).
Get a free key at: https://api.data.gov/signup/
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from services.congress.congress import CongressService
from services.llm.openrouter import OpenRouterService
from services.congress.topics import TOPICS, TOPICS_BY_SLUG

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Configuration ──────────────────────────────────────────────────────────────

CONGRESS = 119   # 119th Congress (2025–2027)
SESSION = 1      # Session 1 (Jan 2025 – Jan 2026); change to 2 for current session

# (label, state, district) — looked up dynamically so bioguide IDs stay fresh
POLITICIANS = [
    ("Alexandria Ocasio-Cortez", "NY", 14),
    ("Marjorie Taylor Greene",   "GA", 14),
    ("Mike Johnson",             "LA",  4),
]

SPONSORED_LIMIT   = 3   # number of sponsored bills to show with full detail
COSPONSORED_LIMIT = 3   # number of cosponsored bills to show with full detail
VOTE_LIMIT        = 10  # number of recent votes to pull per member
BILL_LIST_LIMIT   = 20  # number of recent bills to list
BILL_DEEP_LIMIT   = 10  # number of bills to pull full sub-endpoint data for
CANDIDATE_BILL_LIMIT  = 20  # max sponsored+cosponsored bills to analyze per candidate
RECENT_ANALYSIS_LIMIT = 20  # recent bills from the general bill list to analyze
ALIGNMENT_VOTE_LIMIT  = 50  # number of votes to consider for alignment scoring

W = 76  # display width

# ── Display helpers ────────────────────────────────────────────────────────────

def header(title: str):
    print(f"\n{'═' * W}")
    print(f"  {title}")
    print(f"{'═' * W}")


def section(title: str):
    print(f"\n  ┌─ {title}")
    print(f"  │")


def row(label: str, value, indent: int = 4):
    label_col = 26
    print(f"  │  {label:<{label_col}} {value}")


def divider():
    print(f"  │  {'·' * (W - 6)}")


def section_end():
    print(f"  └{'─' * (W - 3)}")


def truncate(text: str | None, width: int = 60) -> str:
    if not text:
        return "(none)"
    return text if len(text) <= width else text[:width - 1] + "…"


# ── Per-entity display ─────────────────────────────────────────────────────────

def show_bill(bill, detail, label_prefix: str = ""):
    tag = f"{bill.type} {bill.number}"
    print(f"  │  {'─' * (W - 6)}")
    row(f"{label_prefix}{tag}", truncate(bill.title, 44))
    row("Introduced", bill.introduced_date or "(unknown)")
    row("Latest action", bill.latest_action_date or "(unknown)")
    row("Latest action text", truncate(bill.latest_action_text, 44))

    if detail:
        row("Origin chamber", detail.origin_chamber or "(unknown)")
        row("Policy area", detail.policy_area or "(none)")

        if detail.summaries:
            latest_summary = detail.summaries[-1]
            if latest_summary.text:
                # Strip HTML tags crudely for display
                import re
                clean = re.sub(r"<[^>]+>", "", latest_summary.text or "")
                row("Summary", truncate(clean, 44))

        row("Total actions", str(len(detail.actions)))
        if detail.actions:
            most_recent = detail.actions[0]
            row("  Most recent action", truncate(most_recent.text, 44))


def show_vote(vote_record):
    bill_tag = ""
    if vote_record.bill:
        b = vote_record.bill
        bill_tag = f"  [{b.get('type','')} {b.get('number','')}]"
    position_symbol = {
        "Yea": "✓", "Nay": "✗", "Not Voting": "–", "Present": "○"
    }.get(vote_record.member_position, "?")
    print(
        f"  │  Vote {vote_record.vote_number:>4}  "
        f"{vote_record.vote_date or '':>10}  "
        f"{position_symbol} {vote_record.member_position:<11}"
        f"  {truncate(vote_record.vote_question, 30)}"
        f"{bill_tag}"
    )


def show_bill_deep(svc: CongressService, congress: int, bill_type: str, bill_number):
    """Pull and display all sub-endpoint data for a single bill."""
    import re

    tag = f"{bill_type.upper()} {bill_number}"
    detail = svc.get_bill_detail(congress, bill_type, bill_number)
    if not detail:
        row("Bill", f"{tag} — not found")
        return

    print(f"  │  {'─' * (W - 6)}")
    row("Bill", f"{tag}: {truncate(detail.title, 50)}")
    row("Introduced", detail.introduced_date or "(unknown)")
    row("Origin chamber", detail.origin_chamber or "(unknown)")
    row("Policy area", detail.policy_area or "(none)")

    # Summaries
    summaries = svc.get_bill_summaries(congress, bill_type, bill_number)
    row("Summaries", str(len(summaries)))
    if summaries:
        latest = summaries[-1]
        clean = re.sub(r"<[^>]+>", "", latest.text or "")
        row("  Latest summary", truncate(clean, 44))

    # Text versions
    texts = svc.get_bill_text(congress, bill_type, bill_number)
    row("Text versions", str(len(texts)))
    for tv in texts[:3]:
        fmt_urls = [f.get("url", "") for f in (tv.formats or []) if f.get("url")]
        fmt_label = f" ({len(fmt_urls)} formats)" if fmt_urls else ""
        row(f"  {tv.type or '(type?)'}", f"{tv.date or ''}{fmt_label}")

    # Titles
    titles = svc.get_bill_titles(congress, bill_type, bill_number)
    row("Titles", str(len(titles)))
    for t in titles[:3]:
        row(f"  {t.title_type or '?'}", truncate(t.title, 44))

    # Actions
    actions = svc.get_bill_actions(congress, bill_type, bill_number)
    row("Actions", str(len(actions)))
    if actions:
        row("  Most recent", truncate(actions[0].text, 44))

    # Amendments
    amendments = svc.get_bill_amendments(congress, bill_type, bill_number)
    row("Amendments", str(len(amendments)))

    # Committees
    committees = svc.get_bill_committees(congress, bill_type, bill_number)
    row("Committees", str(len(committees)))
    for c in committees[:3]:
        row(f"  {c.chamber or '?'}", truncate(c.name, 44))

    # Cosponsors
    cosponsors = svc.get_bill_cosponsors(congress, bill_type, bill_number)
    row("Cosponsors", str(len(cosponsors)))
    for cs in cosponsors[:3]:
        row(f"  {cs.party or '?'}-{cs.state or '?'}", cs.full_name or cs.bioguide_id)

    # Related bills
    related = svc.get_bill_related_bills(congress, bill_type, bill_number)
    row("Related bills", str(len(related)))
    for rb in related[:3]:
        row(f"  {rb.type or '?'} {rb.number or '?'}", truncate(rb.title, 44))

    # Subjects
    subjects = svc.get_bill_subjects(congress, bill_type, bill_number)
    row("Subjects", str(len(subjects)))
    for s in subjects[:5]:
        row("  •", s.name or "(unnamed)")


# ── Main ───────────────────────────────────────────────────────────────────────

def process_member(svc: CongressService, label: str, state: str, district: int) -> str | None:
    """Process a member and return their bioguide ID (or None if not found)."""
    header(f"{label}  ·  {state}-{district}")

    # ── Look up current member by district ──────────────────────────────────
    print(f"\n  Looking up {state}-{district} in {CONGRESS}th Congress...")
    members = svc.get_members_by_district(CONGRESS, state, district)
    if not members:
        print(f"  [!] No member found for {state}-{district} in congress {CONGRESS}")
        return None
    member_summary = members[0]
    bioguide_id = member_summary.bioguide_id

    # ── Member detail ───────────────────────────────────────────────────────
    detail = svc.get_member(bioguide_id)
    section("Member Info")
    row("Bioguide ID", bioguide_id)
    if detail:
        row("Party", detail.current_party or "(unknown)")
        row("State", detail.state or "(unknown)")
        row("District", str(detail.district) if detail.district else "(unknown)")
        row("Serving since", str(detail.serving_since) if detail.serving_since else "(unknown)")
        row("Born", detail.birth_year or "(unknown)")
        row("Website", detail.official_website_url or "(none)")
    section_end()

    # ── Sponsored legislation ───────────────────────────────────────────────
    print(f"\n  Fetching sponsored legislation...")
    sponsored = svc.get_sponsored_legislation(bioguide_id, congress=CONGRESS)
    section(f"Sponsored Legislation ({len(sponsored)} total in {CONGRESS}th Congress, showing {min(SPONSORED_LIMIT, len(sponsored))})")
    for bill in sponsored[:SPONSORED_LIMIT]:
        bill_detail = svc.get_bill_detail(bill.congress, bill.type, bill.number)
        show_bill(bill, bill_detail)
    if not sponsored:
        print(f"  │  (none in {CONGRESS}th Congress)")
    section_end()

    # ── Cosponsored legislation ─────────────────────────────────────────────
    print(f"\n  Fetching cosponsored legislation...")
    cosponsored = svc.get_cosponsored_legislation(bioguide_id, congress=CONGRESS)
    section(f"Cosponsored Legislation ({len(cosponsored)} total in {CONGRESS}th Congress, showing {min(COSPONSORED_LIMIT, len(cosponsored))})")
    for bill in cosponsored[:COSPONSORED_LIMIT]:
        bill_detail = svc.get_bill_detail(bill.congress, bill.type, bill.number)
        show_bill(bill, bill_detail)
    if not cosponsored:
        print(f"  │  (none in {CONGRESS}th Congress)")
    section_end()

    # ── Voting history ──────────────────────────────────────────────────────
    print(f"\n  Fetching voting history (last {VOTE_LIMIT} votes, Congress {CONGRESS} Session {SESSION})...")
    votes = svc.get_member_voting_history(bioguide_id, CONGRESS, SESSION, limit=VOTE_LIMIT)
    section(f"Voting History — {CONGRESS}th Congress, Session {SESSION} (last {VOTE_LIMIT} votes checked)")
    print(f"  │  {'Vote':>8}  {'Date':>10}  {'Position':<13}  {'Question'}")
    print(f"  │  {'─'*8}  {'─'*10}  {'─'*13}  {'─'*30}")
    if votes:
        for v in votes:
            show_vote(v)
    else:
        print(f"  │  (no votes found — member may not have voted in this batch)")
    section_end()

    return bioguide_id


def process_bills(svc: CongressService):
    """Browse recent bills, pull full content, and print a cache summary."""
    import re

    header(f"Recent Bills — {CONGRESS}th Congress")

    # ── List recent bills ────────────────────────────────────────────────
    print(f"\n  Fetching {BILL_LIST_LIMIT} recent bills...")
    bills = svc.get_bills(congress=CONGRESS, limit=BILL_LIST_LIMIT)
    section(f"Recent Bills ({len(bills)} fetched)")
    for i, b in enumerate(bills, 1):
        print(f"  │  {i:>3}. {b.type} {b.number:<8} {truncate(b.title, 50)}")
    if not bills:
        print(f"  │  (none found)")
    section_end()

    # ── Deep-dive into first N bills ─────────────────────────────────────
    deep_bills = bills[:BILL_DEEP_LIMIT]
    if not deep_bills:
        return

    # Track stats for the summary
    stats = {
        "bills": len(deep_bills), "summaries": 0, "text_versions": 0,
        "titles": 0, "actions": 0, "amendments": 0, "committees": 0,
        "cosponsors": 0, "related": 0, "subjects": 0,
    }

    section(f"Deep Dive — {len(deep_bills)} bills (all sub-endpoints)")
    for b in deep_bills:
        congress, bt, bn = b.congress, b.type, b.number
        tag = f"{bt} {bn}"

        detail = svc.get_bill_detail(congress, bt, bn)
        if not detail:
            print(f"  │  {tag}: not found")
            continue

        print(f"  │")
        print(f"  │  {'━' * (W - 6)}")
        print(f"  │  {tag}: {truncate(detail.title, 58)}")
        print(f"  │  {'━' * (W - 6)}")
        row("Introduced", f"{detail.introduced_date or '?'}  |  Chamber: {detail.origin_chamber or '?'}  |  Area: {detail.policy_area or '(none)'}")

        # Summaries
        summaries = svc.get_bill_summaries(congress, bt, bn)
        stats["summaries"] += len(summaries)
        if summaries:
            latest = summaries[-1]
            clean = re.sub(r"<[^>]+>", "", latest.text or "")
            row("Summary", f"({len(summaries)} version{'s' if len(summaries) != 1 else ''})")
            # Wrap summary text to ~70 chars per line for readability
            words = clean.split()
            line = ""
            for word in words:
                if len(line) + len(word) + 1 > 60:
                    print(f"  │      {line}")
                    line = word
                else:
                    line = f"{line} {word}" if line else word
            if line:
                print(f"  │      {line}")
        else:
            row("Summary", "(none)")

        # Text versions + raw XML
        texts = svc.get_bill_text(congress, bt, bn)
        stats["text_versions"] += len(texts)
        if texts:
            fmt_list = []
            for tv in texts:
                formats = [f.get("type", "?") for f in (tv.formats or [])]
                fmt_list.append(f"{tv.type or '?'} ({', '.join(formats)})" if formats else (tv.type or "?"))
            row("Text", f"{len(texts)} version{'s' if len(texts) != 1 else ''}: {', '.join(fmt_list)}")
        else:
            row("Text", "(none)")

        # Fetch the most recent XML text
        xml = svc.get_bill_text_xml(congress, bt, bn)
        if xml:
            stats["xml_fetched"] = stats.get("xml_fetched", 0) + 1
            # Show a short preview — first non-empty, non-XML-declaration line
            preview_lines = []
            for line in xml.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("<?xml") and not stripped.startswith("<!DOCTYPE"):
                    preview_lines.append(stripped)
                    if len(preview_lines) >= 2:
                        break
            row("XML (cached)", f"{len(xml):,} chars — bills/text_xml/")
            for pl in preview_lines:
                print(f"  │      {truncate(pl, 62)}")
        else:
            row("XML", "(no XML available)")

        # Titles
        titles = svc.get_bill_titles(congress, bt, bn)
        stats["titles"] += len(titles)
        if titles:
            types_seen = set()
            for t in titles:
                types_seen.add(t.title_type or "Unknown")
            row("Titles", f"{len(titles)} ({', '.join(sorted(types_seen))})")
        else:
            row("Titles", "(none)")

        # Actions
        actions = svc.get_bill_actions(congress, bt, bn)
        stats["actions"] += len(actions)
        if actions:
            row("Actions", f"{len(actions)} — latest: {truncate(actions[0].text, 40)}")
        else:
            row("Actions", "0")

        # Amendments
        amendments = svc.get_bill_amendments(congress, bt, bn)
        stats["amendments"] += len(amendments)
        row("Amendments", str(len(amendments)))

        # Committees
        committees = svc.get_bill_committees(congress, bt, bn)
        stats["committees"] += len(committees)
        if committees:
            names = [c.name or "?" for c in committees]
            row("Committees", f"{len(committees)}: {', '.join(truncate(n, 30) for n in names)}")
        else:
            row("Committees", "0")

        # Cosponsors
        cosponsors = svc.get_bill_cosponsors(congress, bt, bn)
        stats["cosponsors"] += len(cosponsors)
        if cosponsors:
            by_party: dict[str, int] = {}
            for cs in cosponsors:
                p = cs.party or "?"
                by_party[p] = by_party.get(p, 0) + 1
            party_str = ", ".join(f"{p}: {n}" for p, n in sorted(by_party.items()))
            row("Cosponsors", f"{len(cosponsors)} ({party_str})")
        else:
            row("Cosponsors", "0")

        # Related bills
        related = svc.get_bill_related_bills(congress, bt, bn)
        stats["related"] += len(related)
        if related:
            tags = [f"{rb.type or '?'} {rb.number or '?'}" for rb in related[:5]]
            more = f" +{len(related) - 5} more" if len(related) > 5 else ""
            row("Related bills", f"{len(related)}: {', '.join(tags)}{more}")
        else:
            row("Related bills", "0")

        # Subjects
        subjects = svc.get_bill_subjects(congress, bt, bn)
        stats["subjects"] += len(subjects)
        if subjects:
            names = [s.name or "?" for s in subjects[:6]]
            more = f" +{len(subjects) - 6} more" if len(subjects) > 6 else ""
            row("Subjects", f"{len(subjects)}: {', '.join(names)}{more}")
        else:
            row("Subjects", "0")

    section_end()

    # ── Laws ─────────────────────────────────────────────────────────────
    print(f"\n  Fetching public laws...")
    laws = svc.get_laws(congress=CONGRESS, law_type="pub", limit=5)
    section(f"Public Laws — {CONGRESS}th Congress ({len(laws)} fetched)")
    for law in laws:
        row(f"Pub.L. {law.number}", truncate(law.title, 44))
    if not laws:
        print(f"  │  (none found)")
    section_end()

    # ── Cache structure summary ──────────────────────────────────────────
    header("Cache Summary")
    print()
    print(f"  Pulled data for {stats['bills']} bills across all sub-endpoints.")
    print(f"  Each endpoint caches to its own folder under data/data/bills/.")
    print()
    print(f"  {'Endpoint':<20} {'Folder':<24} {'Items'}")
    print(f"  {'─' * 20} {'─' * 24} {'─' * 8}")
    xml_count = stats.get("xml_fetched", 0)
    cache_map = [
        ("Bill list",       "bills/list/",        f"{len(bills)} bills"),
        ("Bill detail",     "bills/detail/",      f"{stats['bills']} bills"),
        ("Summaries",       "bills/summaries/",   f"{stats['summaries']} total"),
        ("Full text",       "bills/text/",        f"{stats['text_versions']} versions"),
        ("Raw XML",         "bills/text_xml/",    f"{xml_count} files"),
        ("Titles",          "bills/titles/",      f"{stats['titles']} total"),
        ("Actions",         "bills/actions/",     f"{stats['actions']} total"),
        ("Amendments",      "bills/amendments/",  f"{stats['amendments']} total"),
        ("Committees",      "bills/committees/",  f"{stats['committees']} total"),
        ("Cosponsors",      "bills/cosponsors/",  f"{stats['cosponsors']} total"),
        ("Related bills",   "bills/relatedbills/",f"{stats['related']} total"),
        ("Subjects",        "bills/subjects/",    f"{stats['subjects']} total"),
        ("Laws list",       "laws/list/",         f"{len(laws)} laws"),
    ]
    for label, folder, count in cache_map:
        print(f"  {label:<20} {folder:<24} {count}")
    print()
    print(f"  JSON files = API responses.  XML files = raw bill text for LLM input.")
    print(f"  Example: bills/text_xml/119_hr_28_BILLS-119hr28eh.xml")


def collect_candidate_bills(
    svc: CongressService, bioguide_ids: list[str]
) -> tuple[list[tuple[int, str, str, str]], set[str]]:
    """Collect all sponsored + cosponsored bills for the given members.

    Returns (bills, seen_keys) where bills is a deduplicated list of
    (congress, type, number, title) tuples.
    """
    seen: set[str] = set()
    bills: list[tuple[int, str, str, str]] = []

    for bio_id in bioguide_ids:
        member = svc.get_member(bio_id)
        name = member.name if member else bio_id

        sponsored = svc.get_sponsored_legislation(bio_id, congress=CONGRESS)
        cosponsored = svc.get_cosponsored_legislation(bio_id, congress=CONGRESS)

        # Merge sponsored first, then cosponsored, capped at the per-candidate limit
        member_bills = list(sponsored) + list(cosponsored)
        added = 0
        for b in member_bills:
            if added >= CANDIDATE_BILL_LIMIT:
                break
            key = f"{b.congress}_{b.type}_{b.number}"
            if key not in seen:
                seen.add(key)
                bills.append((b.congress, b.type, b.number, b.title or "(untitled)"))
                added += 1

        print(f"  {name}: {len(sponsored)} sponsored, {len(cosponsored)} cosponsored → {added} added")

    return bills, seen


def analyze_bills(svc: CongressService, bioguide_ids: list[str]):
    """Collect bills from candidates + recent list, then run LLM analysis."""
    header(f"Bill Topic Analysis — {CONGRESS}th Congress")

    # ── Collect candidate bills ───────────────────────────────────────────
    print(f"\n  Collecting bills from {len(bioguide_ids)} candidates...")
    candidate_bills, seen = collect_candidate_bills(svc, bioguide_ids)
    print(f"  → {len(candidate_bills)} unique bills from candidates")

    # ── Add recent bills from general list ────────────────────────────────
    recent = svc.get_bills(congress=CONGRESS, limit=RECENT_ANALYSIS_LIMIT)
    recent_added = 0
    for b in recent:
        key = f"{b.congress}_{b.type}_{b.number}"
        if key not in seen:
            seen.add(key)
            candidate_bills.append((b.congress, b.type, b.number, b.title or "(untitled)"))
            recent_added += 1
    print(f"  → {recent_added} additional from recent bills list")

    all_bills = candidate_bills
    total = len(all_bills)
    print(f"  → {total} total bills to analyze")

    if not all_bills:
        print("  No bills to analyze.")
        return

    # ── Analyze each bill ─────────────────────────────────────────────────
    analyzed = 0
    skipped = 0

    for i, (congress, bt, bn, title) in enumerate(all_bills, 1):
        tag = f"{bt} {bn}"
        print(f"\n  [{i}/{total}] {tag}: {truncate(title, 50)}")

        try:
            analysis = svc.analyze_bill(congress, bt, bn)
        except ValueError as e:
            print(f"         ⊘ No XML available, skipping")
            skipped += 1
            continue

        analyzed += 1
        nonzero = [s for s in analysis.scores if s.score != 0]

        if not nonzero:
            print(f"         No topic impact (all scores = 0)")
            continue

        nonzero.sort(key=lambda s: abs(s.score), reverse=True)
        for s in nonzero:
            bar_len = int(abs(s.score) * 10)
            direction = "+" if s.score > 0 else "−"
            bar = direction * bar_len
            print(f"         {s.score:+5.1f}  {bar:<10}  {s.topic_name}")

    print(f"\n  ── Summary: {analyzed} analyzed, {skipped} skipped (no XML) ──")
    section_end()


def compute_alignment(svc: CongressService, bioguide_ids: list[str]):
    """Compute each member's topic alignment from their voting record.

    For each member:
      1. Pull their recent votes
      2. For each vote on a bill, analyze that bill's topic scores
      3. Numerator: sum of bill topic scores where member voted Yea
      4. Denominator: sum of |bill topic scores| for ALL bills they voted on
      5. Alignment = numerator / denominator (per topic)
    """
    header(f"Member Alignment — {CONGRESS}th Congress, Session {SESSION}")

    for bio_id in bioguide_ids:
        member = svc.get_member(bio_id)
        name = member.name if member else bio_id

        print(f"\n  Fetching votes for {name}...")
        votes = svc.get_member_voting_history(
            bio_id, CONGRESS, SESSION, limit=ALIGNMENT_VOTE_LIMIT
        )

        # Filter to votes that have an associated bill
        bill_votes = [v for v in votes if v.bill]
        print(f"  {len(votes)} votes fetched, {len(bill_votes)} with associated bills")

        # Track per-topic: numerator (yea scores) and denominator (all absolute scores)
        numerator: dict[str, float] = {}
        denominator: dict[str, float] = {}
        bills_analyzed = 0
        bills_skipped = 0

        for i, v in enumerate(bill_votes, 1):
            b = v.bill
            bt = b.get("type", "").lower()
            bn = b.get("number", "")
            if not bt or not bn:
                continue

            tag = f"{bt.upper()} {bn}"
            print(f"    [{i}/{len(bill_votes)}] {tag} — {v.member_position}", end="")

            try:
                analysis = svc.analyze_bill(CONGRESS, bt, bn)
            except (ValueError, RuntimeError):
                print(" ⊘ skipped (not yet analyzed)")
                bills_skipped += 1
                continue

            bills_analyzed += 1
            print()

            for s in analysis.scores:
                if s.score == 0:
                    continue
                # Every voted-on bill contributes to the denominator
                denominator[s.topic_slug] = denominator.get(s.topic_slug, 0) + abs(s.score)
                # Only Yea votes contribute to the numerator
                if v.member_position == "Yea":
                    numerator[s.topic_slug] = numerator.get(s.topic_slug, 0) + s.score

        # ── Display alignment ─────────────────────────────────────────────
        section(f"{name} — Alignment ({bills_analyzed} bills scored, {bills_skipped} skipped)")

        if not denominator:
            print(f"  │  No scoreable bills in voting record")
            section_end()
            continue

        alignments = []
        for slug, denom in denominator.items():
            numer = numerator.get(slug, 0)
            alignment = numer / denom if denom else 0
            topic_cfg = TOPICS_BY_SLUG.get(slug)
            topic_name = topic_cfg.name if topic_cfg else slug
            alignments.append((topic_name, alignment, numer, denom, slug))

        # Sort by absolute alignment descending
        alignments.sort(key=lambda x: abs(x[1]), reverse=True)

        for topic_name, alignment, numer, denom, slug in alignments:
            bar_len = int(abs(alignment) * 10)
            direction = "+" if alignment > 0 else "−"
            bar = direction * bar_len
            print(f"  │  {alignment:+6.2f}  {bar:<10}  {topic_name}  ({numer:+.1f}/{denom:.1f})")

        # Topics with no signal
        topics_with_signal = {slug for _, _, _, _, slug in alignments}
        silent = [t.name for t in TOPICS if t.slug not in topics_with_signal]
        if silent:
            print(f"  │")
            print(f"  │  No signal: {', '.join(silent)}")

        section_end()


STEPS = {
    "1": ("Fetch member info & voting history", "members"),
    "2": ("Fetch & explore recent bills",       "bills"),
    "3": ("LLM bill topic analysis",            "analyze"),
    "4": ("Compute member alignment scores",    "alignment"),
}


def show_menu() -> set[str]:
    """Display step menu and return selected step keys."""
    print(f"\n{'═' * W}")
    print("  Steps:")
    for num, (desc, _) in STEPS.items():
        print(f"    {num}. {desc}")
    print(f"    a. Run all")
    print(f"{'═' * W}")
    choice = input("\n  Select steps (comma-separated, e.g. 1,4 or a): ").strip().lower()
    if choice == "a" or choice == "":
        return {key for key in STEPS}
    return {c.strip() for c in choice.split(",") if c.strip() in STEPS}


def resolve_bioguide_ids(svc: CongressService) -> list[str]:
    """Resolve bioguide IDs for all configured politicians (uses cache)."""
    ids = []
    for label, state, district in POLITICIANS:
        members = svc.get_members_by_district(CONGRESS, state, district)
        if members:
            ids.append(members[0].bioguide_id)
        else:
            print(f"  [!] No member found for {label} ({state}-{district})")
    return ids


def main():
    api_key = os.getenv("CONGRESS_API_KEY", "DEMO_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if api_key == "DEMO_KEY":
        print(
            "\n[warning] Using DEMO_KEY — rate limits apply. "
            "Set CONGRESS_API_KEY in .env for full access.\n"
        )

    selected = show_menu()
    if not selected:
        print("  No steps selected.")
        return

    step_keys = {STEPS[num][1] for num in selected}
    needs_llm = "analyze" in step_keys

    llm = None
    if needs_llm:
        if openrouter_key and openrouter_key != "put_key_here":
            llm = OpenRouterService(api_key=openrouter_key)
        else:
            print("\n[!] OPENROUTER_API_KEY not set — cannot run LLM analysis.\n")
            step_keys.discard("analyze")

    svc = CongressService(api_key=api_key, llm_service=llm)

    # Resolve bioguide IDs if any member-related step is selected
    bioguide_ids: list[str] = []
    if step_keys & {"members", "analyze", "alignment"}:
        bioguide_ids = resolve_bioguide_ids(svc)

    if "members" in step_keys:
        for label, state, district in POLITICIANS:
            process_member(svc, label, state, district)

    if "bills" in step_keys:
        process_bills(svc)

    if "analyze" in step_keys and llm:
        analyze_bills(svc, bioguide_ids)

    if "alignment" in step_keys:
        compute_alignment(svc, bioguide_ids)

    print(f"\n{'═' * W}")
    print("  Done.")
    print(f"{'═' * W}\n")


if __name__ == "__main__":
    main()
