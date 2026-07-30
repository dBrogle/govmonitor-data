# Fundraising Data Quality & PAC Profile — Investigation & Plan

*Investigation date: 2026-06-02. Scope: the campaign-finance side of the pipeline
(`data/services/campaign_finance/`, `data/pipeline/stages/s2_finance.py`) and how it
surfaces in the web app (`FinanceSection.tsx`). Goal: address the pain point that the
**volume and quality of fundraising data is low**, and design the **PAC profile** feature
from `notes.txt` ("V1: Make pac profile based on who it donates to and who funds it").*

---

## TL;DR

The fundraising data is thin and, in places, **silently broken**. Three concrete proofs
from the current output:

1. **"Top PAC Contributors" is empty for all 3 candidates** (0 / 0 / 0) — even Mike
   Johnson, who took **$1.79M** in PAC money. The section never renders. This is a bug,
   not a data-availability problem.
2. **The "Other" funding bucket is huge and meaningless.** AOC: $15.3M raised, only
   $4.26M itemized individual + $0.04M PAC → **~$11M (72%) dumped into gray "Other."**
   That $11M is overwhelmingly *small-dollar unitemized* donations — which is the single
   most important thing about how she funds her campaign — and we hide it.
3. **~5 of the 7 finance arrays we collect are never shown.** `independent_expenditures`,
   `electioneering`, `contributions_by_employer`, `committees`, and `committee_details`
   are fetched, stored, shipped to the frontend… and never rendered.

There is also **no PAC profile at all** yet. We store a PAC's *identity*
(`CommitteeDetail`: type, org type, connected org) but nothing about its *behavior* —
who it funds, who funds it, its size, or its lean. That's the V1 feature, and it depends
on fixing the collection layer first.

---

## Current state (what we actually have)

Per candidate, `s2_finance` produces:

| Field | AOC | MTG | Johnson | Notes |
|---|---|---|---|---|
| `totals.receipts` | $15.3M | $8.96M | $19.9M | from `candidates/totals/` |
| `totals.individual_itemized_contributions` | $4.26M | $3.07M | $5.45M | |
| `totals.other_political_committee_contributions` (PAC) | $0.04M | $0.04M | $1.79M | |
| `committees` | 1 | 1 | 1 | only principal captured |
| `top_pac_contributions` | **0** | **0** | **0** | ← broken |
| `top_individual_contributions` | 14 | 18 | **1** | capped + skewed |
| `contributions_by_employer` | 10 | 10 | 10 | collected, **not rendered** |
| `independent_expenditures` | 5 | 4 | 5 | collected, **not rendered** |
| `electioneering` | 0 | 0 | 1 | collected, **not rendered** |

Only **3 candidates**, only the **2024 cycle**, only the **principal committee**.

---

## The gaps, with root causes

### G1 — PAC contributions silently come back empty *(highest priority — it's a bug)*
`s2_finance` calls `get_contributions(committee_id, limit=20)`, which fetches the **top 20
receipts by dollar amount** for a committee, then filters client-side for `line_number ==
"11C"` (the PAC line). But the top 20 receipts are dominated by **transfers (line 12)** and
**large itemized individuals (11AI)**. Verified in the cache: most committees' top-20 are
all 12/11AI, so the 11C filter yields nothing. Johnson has $1.79M in PAC money and we
surface **zero** PACs.
**Root cause:** wrong query strategy + tiny limit. We should query PAC receipts
*server-side* (filter by line number / contributor type at the API), not fetch-then-filter
a handful of top receipts.

### G2 — The "Other" bucket swallows the most important story
The frontend computes `other = receipts − itemized_individual − PAC`. For AOC that's ~$11M
(72%). The dominant component is **unitemized small-dollar (<$200) contributions** — her
grassroots base — plus transfers, loans, and offsets. We can't break it down because the
`candidates/totals/` endpoint we use returns only a handful of fields (verified: it has no
`individual_unitemized_contributions`). The **`committee/{id}/totals/`** endpoint does
expose the full breakdown (itemized, unitemized, party, PAC, transfers, candidate loans,
offsets, refunds). `notes.txt` literally lists this: *"Get all funding listed (no other,
values add up properly)."*
**Root cause:** wrong endpoint (candidate totals vs committee totals).

### G3 — We only capture the principal committee
All candidates show 1 committee. Big donors route money through **Joint Fundraising
Committees (JFCs)** and **leadership PACs**, then transfer to the principal — which is why
the principal's top receipts are "transfers" (line 12). By not walking into the JFC's own
Schedule A, the *original* large donors are invisible (this is why Johnson shows only **1**
individual contributor — his receipts are mostly transfers). The pipeline fetches the
committee list but the downstream logic effectively uses one committee.
**Root cause:** no traversal from principal → JFC/leadership PAC → original sources.

### G4 — No PAC profile (the V1 feature)
We store `CommitteeDetail` (identity) but nothing about a PAC's behavior. To answer "who
does this PAC fund and who funds it" we need, per PAC: its **receipts total**, **top
recipients** (Schedule B / candidate disbursements), **top funders** (Schedule A), partisan
split of recipients, and committee type/lean. None of this is collected today.
**Root cause:** feature not built; also blocked by G1 (we don't even reliably know which
PACs to profile).

### G5 — Collected-but-invisible data
`FinanceSection.tsx` renders only totals, the funding-source bar, top PACs, and top
individuals. `independent_expenditures` (outside spending for/against — the "dark money"
picture, incl. support/oppose split), `electioneering`, and `contributions_by_employer`
are all in the shipped JSON but **never displayed**. Cheap, high-value visibility wins.

### G6 — Thin coverage: limits, cycles, candidate count
`contribution_limit = 20` caps both top-PAC and top-individual lists and is the mechanical
cause of G1's skew. Only the **2024 cycle** is collected — no career totals, no multi-cycle
trend, so there's no sense of trajectory. And only **3 candidates** exist, so "volume" is
low in the most literal sense.

### G7 — Data-integrity guards are missing
Nothing checks that displayed components sum to receipts, that "Other" stays within a sane
bound, or flags when PAC dollars exist in totals but `top_pac_contributions` is empty (which
would have caught G1 immediately). No freshness/cycle stamp shown to users either.

---

## Proposals (each with pros / cons)

### P1 — Fix PAC contribution collection (server-side, by line/type)  **[do first]**
Replace the fetch-top-20-then-filter approach with a query that asks the API for PAC
receipts directly (Schedule A filtered by `line_number`/contributor committee type, sorted
by amount, paginated), or use a by-contributor aggregate endpoint. Raise `contribution_limit`.
- **Pros:** Fixes the empty "Top PAC" section for everyone; unblocks the PAC profile (G4);
  small, well-scoped change in `fec.py` + `s2_finance.py`.
- **Cons:** More API calls (pagination) → slower runs, more rate-limit exposure; need to
  re-validate the line-number taxonomy against live data.

### P2 — Switch to committee totals to eliminate "Other"  **[high value, low effort]**
Add `get_committee_totals(committee_id, cycle)` against `committee/{id}/totals/`, aggregate
across the candidate's committees, and expose the full breakdown: itemized individual,
**unitemized individual (small-dollar)**, PAC, party, transfers, candidate self-funding,
offsets. Replace the frontend's `other = receipts − a − b` with real categories.
- **Pros:** Kills the meaningless 72% gray bucket; surfaces small-dollar grassroots vs
  large-donor vs PAC vs self-funding — a genuinely insightful, "engaging" comparison;
  directly satisfies the `notes.txt` "no other, values add up" requirement.
- **Cons:** Must reconcile candidate-level vs committee-level numbers (they can differ
  slightly); summing across multiple committees risks double-counting transfers — need a
  netting rule.

### P3 — Build the PAC profile  **[the headline feature; depends on P1]**
For each significant PAC, collect and store a profile: identity (have it), size (receipts),
**top recipients** with partisan split (who it funds), **top funders** (who funds it), and a
derived lean/label. Render as an expandable card under "Top PAC Contributors" and/or a
dedicated `/pac/:id` page.
- **Pros:** Turns an opaque committee name into a story ("Corporate trade PAC, 90%
  Republican recipients, funded by X industry") — exactly the transparency mission; reusable
  across every candidate who shares that PAC.
- **Cons:** Most expensive in API calls and storage (each PAC needs its own Schedule A/B
  pulls); needs caching/dedup so shared PACs aren't refetched per candidate; "lean"
  classification is a judgment call (could use the existing LLM service, which adds cost).
- **Sub-decision:** *inline expandable card* (simpler, less navigation) vs *dedicated PAC
  pages* (richer, shareable, more build). Recommend inline first, page later.

### P4 — Render the data we already collect  **[quick win]**
Add frontend sections for independent expenditures (with a support-vs-oppose visual),
electioneering, and top employers. No pipeline change required.
- **Pros:** Immediate increase in *perceived* volume and real insight (outside spending is
  often the most newsworthy money); near-zero risk.
- **Cons:** Independent-expenditure data is currently sparse (4–5 rows) — may look thin until
  P1/P5 deepen it; more UI surface to design well (ties into the `notes.txt` "fundraising
  needs more info and cleaning up" item).

### P5 — Traverse JFCs / leadership PACs to original donors  **[deeper, optional]**
From the principal committee, follow transfers (line 12) into JFCs and leadership PACs and
pull *their* Schedule A to recover the real large donors.
- **Pros:** Fixes the "Johnson has 1 individual donor" absurdity; reveals the donors that
  routing currently hides; materially increases real volume.
- **Cons:** Most complex (graph traversal, dedup across committees, attribution rules);
  meaningfully more API load; easy to double-count without care.

### P6 — Multi-cycle / career totals  **[breadth]**
Pull more than the 2024 cycle and show a trend (raised per cycle, PAC-share over time).
- **Pros:** Adds a time dimension — trajectory and incumbency advantage become visible;
  cheap-ish (totals endpoints are light).
- **Cons:** More storage and a frontend trend viz to build; older cycles have data-quality
  quirks; marginal vs P1–P4 for the current pain point.

### P7 — Integrity checks & freshness metadata  **[cheap insurance]**
Add pipeline assertions (components reconcile to receipts within tolerance; warn if PAC
dollars > 0 but PAC list empty) and stamp each finance blob with cycle + fetch date shown in
the UI.
- **Pros:** Would have caught G1 automatically; builds trust; tiny effort.
- **Cons:** Tolerances need tuning to avoid noisy warnings.

---

## Shipped (2026-06-02): P2 + P4 + P7 quick-win batch

- **P2 — "Other" bucket eliminated.** New `committee/{id}/totals/` fetch
  (`FECService.get_committee_totals`) + `build_funding_breakdown` in `s2_finance` decompose
  receipts into labeled buckets (small-dollar, large individual, PAC, party, transfers,
  self-funding, other) that sum to receipts. Verified: AOC & Johnson reconcile with **$0.00
  unaccounted**; AOC's previously-hidden **$10.6M small-dollar** is now its own wedge.
- **P4 — collected data now rendered.** `FinanceSection` gained Outside Spending (independent
  expenditures with support-vs-oppose bar), Top Donor Employers, and Electioneering sections.
- **P7 — integrity + freshness.** `s2_finance` emits `data_quality_warnings` (e.g. "PAC money
  in totals but 0 PAC contributors captured" — fires for all 3, flagging the P1 bug) and a
  `fetched_at` stamp shown in a UI source footer. Breakdown residual is asserted < 1% of receipts.
- **No backend migration needed** — candidate JSON is stored whole as `full_json TEXT`.

**Known limitations surfaced by this work (follow-ups):**
- **MTG has no principal committee in our data** — her only captured committee is a leadership
  PAC (designation "D"), so she has no committee-totals breakdown and falls back to the coarse
  candidate-totals split (still shows a large "Other"). Root cause is the committee-collection
  gap (G3), to be fixed with P5.
- **The candidate registry has ~20 names but only 3 have member data** — confirms the literal
  low-volume issue; tracked as the separate candidate-count effort.

## Shipped (2026-06-02): P1 — PAC collection fixed

- **Root cause fixed.** `s2_finance` no longer fetches the top-20 receipts and hopes a line-11C
  PAC survives a client-side filter. New `FECService.get_pac_contributions` filters Schedule A
  **server-side** by form-line (`F3-11C` / `F3X-11C`) via a page-capped `_get_pages` helper, and
  `s2_finance` aggregates per contributor (a PAC's cycle total is split across primary/general
  receipts). New config knob `pac_contribution_pages` (default 5).
- **Result:** PAC contributors went from **0/0/0 → 10/10/10** for the three main candidates, with
  sensible profiles (AOC: labor unions; Johnson: $50K corporate leadership PACs; MTG: right-leaning
  PACs). Across the full registry, **20 of 21** candidates now have ≥1 PAC contributor.
- **Robustness fix (found via a real timeout crash):** committee-detail fetches for top PACs and
  independent spenders were unguarded — a single `ReadTimeout` aborted the entire run before the
  alignment stage, leaving stale frontend files. Both loops are now individually try/excepted, so a
  timeout degrades one PAC's enrichment instead of killing the pipeline.
- **Remaining edge cases (minor):** MTG still lacks a principal committee (G3 — needs P5); Ro Khanna
  reports $2K PAC money with 0 captured (likely filed off line 11C). The data-quality warnings now
  surface exactly these, as intended.

## Shipped (2026-06-02): P3 — PAC profiles (card first)

- **Standalone `PACProfile` per committee** (cached to `output/s2_finance/pac_profiles/{id}_{cycle}.json`),
  so profiles are shared across candidates and ready to back a future `/pac/:id` page. Embedded into
  each candidate's finance blob as `pac_profiles` (keyed by committee_id) for the inline card.
- **What each profile holds:** identity (type, org, connected org), size (receipts/disbursements/cash),
  **who it funds** (top recipients via `schedule_b/by_recipient_id`), **how it's funded** (donation-size
  bands via `schedule_a/by_size`), and a **rules-based partisan lean** — recipient party split, where each
  recipient's party is resolved with one committee-detail lookup (party/candidate committees resolve;
  PAC-to-PAC is unclassified). Lean labels: Democratic / Leans Democratic / Bipartisan / Leans
  Republican / Republican / Unclassified.
- **Scope per decision:** profiles built for **donor PACs *and* outside spenders**; lean is **rules-based**
  (no LLM); gated to renderable candidates (member data present) to bound the first run.
- **Frontend:** `PacProfileCard` + expandable rows in `FinanceSection` (Top PAC Contributors and Outside
  Spending). Click a PAC → see lean badge, who it funds (party-dotted), how it's funded.
- **Verified leans are sensible:** AOC's labor PACs → Democratic; MTG's Freedom Caucus Fund / MC PAC →
  Republican; Johnson's Council of Insurance Agents → **Bipartisan** (funds both DCCC and NRCC). 15 / 11 /
  14 profiles for the three candidates. TSC clean.
- **Fixes found along the way:** `schedule_a/by_size` returns `null` for `size`/`count` on some bands →
  made those model fields nullable and coerce in the builder; recipient-party lookups are individually
  guarded so a timeout leaves a PAC unclassified rather than dropping the profile.

## Shipped (2026-06-02): P5 — JFC / leadership-PAC traversal + dedicated PAC page

- **P5:** `s2_finance` now follows Schedule A line-12 transfers from the principal committee
  into the JFCs/leadership funds that fed it (`FECService.get_transfer_sources`), and pulls
  individual donors + employer aggregates from those committees too. **Johnson went from 1 →
  20 individual donors** (Betty McKee $1M, etc. — previously hidden behind a single transfer
  line). Employer aggregation now dedupes across committees.
- **Known limitation:** MTG still has no donor recovery — her FEC candidate id (H0GA06192)
  has *no* principal committee linked at FEC (`principal_committees` is empty; only a
  leadership PAC resolves). This is a candidate-registry/ID issue, not P5; fix = update her
  `fec_id` in `candidates.json` to her current GA-14 committee. Tracked separately.
- **Dedicated `/pac/:id` page:** PAC profiles are now seeded into their own deduped
  `pac_profiles` table and served via `GET /api/pac/:committeeId`; new `PacDetailPage` reuses
  `PacProfileCard`, and the inline card links to it ("View full PAC profile →").
- **ComparePage** migrated to React Query (search via `useCandidates`, comparison via fixed
  `useCandidate` slots) — now shares the per-candidate cache with the detail page.

## Recommended sequencing

1. **P2 + P4** — biggest visible improvement for least effort and zero new feature risk
   (kill "Other", show the data we already have). Plus **P7** alongside (cheap).
2. **P1** — fix the PAC-collection bug; unblocks the headline feature.
3. **P3** — build the PAC profile (inline card first).
4. **P5 / P6** — deeper traversal and multi-cycle breadth once the core is solid.

## Decisions (locked 2026-06-02)

- **PAC profile depth:** **Both, card first.** Ship the inline expandable card under "Top
  PAC Contributors" now (size, top recipients w/ party split, top funders); design toward a
  dedicated `/pac/:id` page later. Build P3 so the data layer already supports a future page.
- **PAC scope:** **Direct donors *and* outside spenders.** Profile PACs that contribute
  directly (line 11C) *and* Super PACs / groups doing independent expenditures for/against
  the candidate (the dark-money picture). This couples P3 with P4's independent-expenditure
  rendering — the same PAC profile card should be reachable from both the "Top PAC
  Contributors" and "Outside Spending" sections.
- **Lean/label:** **Rules-based.** Derive lean from recipient party split + committee type;
  deterministic, no LLM cost. (LLM prose summaries are a possible later enhancement.)
- **Candidate count:** **Separate effort.** This work targets data depth/quality, not roster
  size. Scaling beyond 3 candidates is tracked separately.

### Implications of these decisions

- P3 must collect, for *both* donor PACs and outside-spender committees: receipts total,
  top recipients (Schedule B / candidate disbursements) with party tally, and top funders
  (Schedule A). Store as a standalone `PACProfile` keyed by committee ID so it's shared
  across candidates and reusable by a future `/pac/:id` page.
- The rules-based lean = derived from the recipient party split (e.g. ≥X% to one party →
  labeled), with committee type ("Super PAC", "Corporation", "Labor") shown alongside.
- Dedup is now required up front: a PAC that funds multiple tracked candidates, or appears
  as both a donor and an outside spender, should be fetched/stored once.
- **Cycles:** 2024-only for now, or is multi-cycle trend in scope soon?
