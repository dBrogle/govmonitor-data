# Alignment Quality Plan

Plan for improving the quality of candidate stance/alignment scores. Companion to
`FUNDRAISING_DATA_PLAN.md`. Budget for the LLM work: **~$20**.

## The problem (worked example)

Barry Moore (AL-1, Republican) shows as **100% "Pro LGBT+ Legal Protections"** — the
maximally progressive position for a deep-red member. Tracing it end to end revealed three
stacked failures, each general (not specific to this case):

1. **We score the parent bill, not what was actually voted on.** His two roll calls were
   *amendment votes* on HR 3838 (the FY2026 NDAA), not final passage. But we applied the
   whole-bill LGBTQ score (`+1.0`, assigned by the LLM off one buried gender-transition
   provision) to both votes. A defense-bill amendment vote got read as an LGBTQ stance.

2. **An "Aye/No" vs "Yea/Nay" string bug silently drops votes.** Committee-of-the-Whole
   votes report `Aye`/`No`; final passage reports `Yea`/`Nay`. The scorer only checks
   `position == "Yea"`, so his `Aye` on vote 262 was thrown out (`contributed_to_alignment:
   false`). Only the lone `Nay` survived → numerator −1.0 / denominator 1.0 → −1.0 =
   "100% pro-LGBT." One unrecognized string flipped the sign of his entire LGBTQ stance.

3. **No confidence / sample size.** n=1 produces the same "100%" headline as 50 consistent
   votes would. The most extreme scores are often the *least* supported.

## Guiding principle

A stance is an **estimate with a confidence, built from many weighted signals** — not an
average of whatever happened to be in the last 50 roll calls.

---

## Phase 1 — Structural vote fixes (start now, ~free)

No new data, no LLM spend. Fixes the Barry Moore class of bug directly.

- **1a. Fix the Aye/No bug.** Treat `Aye`≡`Yea` and `No`≡`Nay` in
  `s5_alignment.py` (the `position ==` checks). This is a live data-corruption bug today.
- **1b. Resolve votes to what they were actually on.** A roll call is on a *question*
  (amendment / motion to recommit / procedural rule / final passage), not "the bill."
  - Final passage → score the bill (as today).
  - Amendment vote → score the **amendment's** text/purpose (we already fetch
    `data/bills/amendments/*.json` with descriptions, purposes, and roll-call mappings),
    not the parent bill.
  - **Weight by vote type:** final passage + substantive amendments count; procedural votes
    (ordering the previous question, motions to adjourn, rules) are party-discipline noise —
    heavily discount or drop.
- **1c. Attach confidence / sample size** to every topic score so thin records can't
  produce an extreme headline. Gate display below a minimum signal threshold.

---

## Phase 2 — Add sponsored/cosponsored legislation (the new data)

Sponsorship is **revealed preference** — often stronger signal than a forced-choice vote.

### Data volume (measured, House-only, 119th Congress)

| Set | Unique bills |
|---|---|
| Currently analyzed (floor-voted + text) | 1,367 |
| Sponsored (unique) | 10,031 |
| Cosponsored (unique) | 4,025 |
| — cosponsored-only (not also sponsored by a tracked member) | **153** |
| Sponsored ∪ cosponsored | 10,184 |
| **Net-new to analyze** | **8,867** |

Key facts:
- **Cosponsorship is ~free on bill count** — only 153 of 4,025 cosponsored bills aren't
  already someone's sponsored bill. Sponsored ≈ the whole universe.
- **House-only.** No `S`/Senate bills appear in our members' sponsorship lists.
- **Per-member lists are cap-saturated** (420/430 members maxed at 50 cosponsors), so 10,184
  is a *floor* — lifting caps grows it.
- **All 119th Congress** (introduced 2025–2026), so no recency filtering applies *yet*.

### Scope-slimming levers (House-only)

Ranked by leverage; free ones first.

1. **`policyArea` prefilter — free, already on disk, and it fixes the Barry Moore bug.**
   Congress.gov stamps one policy area per bill (`data/bills/subjects/`).
   - **Skip bills mapping to no topic** (~20%: "Congress", "Native Americans",
     "Arts/Religion", post-office namings, private bills) at zero LLM cost.
   - **Score only the relevant topics** per bill instead of all 17 — the biggest output-cost
     cut (output dominates spend). Side benefit: the NDAA's area is "Armed Forces", not
     "Civil Rights", so we'd never have asked about LGBTQ on it → the spurious score that
     started this whole investigation never gets generated.
   - Caveat: `policyArea` is a single category. For cross-cutting bills, also pull the
     multi-term legislative `subjects` list, or always include a few high-salience topics.
2. **Summary-first input.** Use the CRS summary (~290 tok) instead of full XML (~6,800 tok)
   where available — a ~23× input shrink. **Coverage caveat:** only ~40% of bills have a CRS
   summary (CRS lags introduction; minor bills often never get one), so **full-text
   fallback** is required. Record provenance: `text_source: "summary" | "full_text"`,
   `full_text_loaded: false`, so we can selectively backfill later.
3. **Lean output.** Emit a reasoning paragraph only for non-zero topics, not all 17.
4. **Cosponsor/seriousness threshold (optional, deeper cut).** Drop the long tail of solo
   bills with ~0 cosponsors and no committee action; keep bills with real cosponsor
   coalitions. Sacrifices some coverage for cost.

### Weighting (revealed preference)

- **Sponsor** = strong endorsement (high weight). **Lead/original cosponsor** > late add-on >
  plain cosponsor.
- **Dedup identical companions.** ~107 House-to-House "identical bill" pairs (~1% of the set).
  Don't double-count a member who backs both twins — collapse to one policy per member. (Not
  worth deduping for *cost* — saves only ~$2–3 — but worth it for weighting honesty.)
- **Keep dead / in-progress bills.** Sponsoring *is* the signal regardless of whether the bill
  passed; dropping unpassed bills would discard most of the value being added.
- **Keep related/absorbed bills as separate signals** (e.g. a standalone bill whose text is
  inserted into an omnibus) — sponsoring it and voting on the omnibus are two distinct acts.

### Stance vs. impact (two distinct metrics)

**~79% of sponsored bills die in committee; only ~2.5% ever get a recorded floor vote.** A
member's stance (what they'd do) and their impact (what they actually moved) are different
questions, and individual members usually *can't* force their own bill to the floor — the
majority leadership, Rules Committee, and committee chairs control the calendar by design.
So don't filter dead bills out; score them all for **stance**, and add a separate **impact**
dimension that weights by how far each bill got and by the sponsor's leverage.

**Per-bill impact score** (all signals available or now fetched):
1. **Progress stage** *(have it — `actions`)*: became law ≫ passed a chamber ≫ reported out of
   committee ≫ died in committee. The backbone. "Reported out of committee or beyond" is the
   natural "had a real shot" threshold (escapes the ~79% graveyard).
2. **Cosponsor coalition** *(have it; lift the 20-cap)*: size + **bipartisan D/R split**.
3. **Companion momentum** *(have it)*: an identical Senate bill = coordinated bicameral push.
4. **Amendments adopted** *(have it)*: a rank-and-file member's real workaround for a locked
   floor — getting an amendment agreed-to is genuine impact.
5. **Sponsor's institutional power** *(NEW — `pipeline/output/member_roles.json`)*: committee
   chair / ranking member / subcommittee chair / party leadership / plain member. The single
   best predictor of whether a bill moves. Source: `scripts/fetch_member_roles.py` pulls the
   `unitedstates/congress-legislators` dataset (Congress.gov doesn't expose committees/
   leadership). Resolved for 426/430 members: 30 full-committee chairs, 22 ranking, 115
   subcommittee chairs, 10 party leadership.

**Member-level effectiveness score**: aggregate how far a member's *sponsored* bills
progressed, weighted by stage — mirrors the academic **Legislative Effectiveness Score**
(Center for Effective Lawmaking, Volden & Wiseman). Useful both as a display metric and as an
external benchmark to validate our numbers against. Bonus jurisdiction signal: a sponsor whose
bill falls in *their own committee's* `policyArea` had real power to advance it.

---

## Cost model & the $20 budget

Cost is **output-dominated**. Matrix for the 8,867 new bills, by model tier:

| Approach | grok-4.3 | mid-tier | cheap (flash-class) |
|---|---|---|---|
| A. Current (17 topics, full text) | $268 | $33 | $12 |
| B. Targeted topics + summary-first | $95 | $13 | $4.40 |
| C. B + `policyArea` prefilter (~20% dropped) | $76 | $11 | $3.50 |

(Assumptions: grok ≈ $3/$15 per M in/out; mid ≈ $0.5/$1.5; cheap ≈ $0.15/$0.6. Verify live
OpenRouter rates. Reasoning-token models can inflate output cost — watch for it.)

### Recommended spend: two-tier scoring (~$5–8)

The **structural fixes (Phase 1) + `policyArea` targeting + adding sponsorship** improve
quality far more than model tier does. So don't spend frontier dollars on every bill:

1. **Bulk/triage pass** — run approach **C on a cheap model** over all ~7,000 bills (~$3.50).
2. **Verification pass** — escalate **only bills that scored non-zero on a topic** (the ones
   that actually move an alignment number) to a frontier model. Most bills score 0 on most
   topics, so this set is small. Frontier dollars land only where the score matters.

Leaves ~$12 in reserve for re-runs while tuning topics. Simpler fallback if you don't want
escalation logic: **C on a mid model, one pass (~$11)**, comfortably under budget.

### Model choices (all via OpenRouter — REST, model-agnostic)

Requirement: reliable **strict `json_schema` structured output** (the service sends
`response_format: json_schema, strict: true`) + large context for full-text fallback on
omnibus bills.

- **Bulk/triage tier:** `google/gemini-2.5-flash` — cheap, fast, ~1M context (swallows the
  9 MB NDAA without truncation), reliable structured output. Cheapest viable alternative:
  `deepseek/deepseek-chat`. Also fine: `openai/gpt-5-mini`-class, `x-ai/grok-4-fast`.
- **Verification tier:** keep **`x-ai/grok-4.3`** (current, already trusted here), or
  `google/gemini-2.5-pro`, or `anthropic/claude-sonnet-4.x` for nuanced political judgment.
- Confirm strict-structured-output support per model on OpenRouter before a big batch — not
  all open models honor `strict: true` and silently degrade to best-effort JSON.

---

## Recency guardrail (build now, fires later)

A "drop bills introduced > 2 years ago" filter is a **no-op today** (everything is 119th
Congress, 2025–26). It becomes relevant when we add the **118th Congress** (2023–24, a
shipping goal in `notes.txt`). Build the 2-year window now so it's ready; it just won't fire
yet. (A 1-year window would cut ~3,000 bills but disproportionately drops Jan–Feb 2025
*flagship* legislation that members front-load at the start of a Congress — avoid for the
current Congress; use only as a smoke-test subset if desired.)

---

## Download as a separate track (long pole)

Fetching detail + summaries/text for the ~7,000 (post-prefilter) bills is the slow step:
~3 Congress.gov calls/bill (detail, text-list, XML/summary), rate-limited to 5,000/hr per
api.data.gov key. **~2–10 hr depending on key-pool size.** It only populates the cache, so it's
safe to run independently while Phase 1 + scoring logic are built in parallel. Disk footprint
is small (~0.1 GB summary-first, ~0.3 GB with full text).

**Recommendation:** kick the download off in a separate session/thread first, then do the
scoring work against the cache as it fills.

**Status / decision (launched):** scope set to the **past year** (`--since 2025-06-08`) to
slim the batch — ~5,800 bills to fetch. Runner: `data/scripts/fetch_sponsorship_bills.py`
(reuses the s3_bills fetch path, resumable, 3-key pool ≈ 15k req/hr, ~3–4 hr). Logs to
`data/pipeline/fetch_sponsorship.log`. Note this 1-year window does drop early-2025 flagship
bills (see guardrail below) — acceptable trade for the slimmer first pass; widen to 2 years
later if coverage matters.

---

## Suggested order

1. **Phase 1** (Aye/No fix, vote→amendment resolution, confidence) — ✅ done. Substantive
   `voteQuestion` read from cached vote headers; `services/congress/vote_questions.py`
   classifies passage (counts) vs procedural/amendment (excluded for now); s5 applies
   Aye≡Yea/No≡Nay, weights by vote class, and attaches per-topic `confidence` +
   `contributing_vote_count`. Barry Moore LGBTQ corrected −1.0 → +1.0 (low confidence).
   Aggregate: 47% of topic-scores are low-confidence (<3 votes) — motivates Phase 2.
2. **Start the bill download** in a separate thread (parallel track). ✅ launched.
3. **Member roles fetch** — ✅ done (`scripts/fetch_member_roles.py` → `member_roles.json`).
4. **Phase 2** scoring against the cache: `policyArea` prefilter + targeting, summary-first
   + provenance flags, lean output, two-tier model pass.
   - ✅ **Full-text-first input** (`_select_analysis_input`): full text → CRS summary (only
     when full text overflows context, e.g. the NDAA at ~1.26M tok) → truncated full text,
     with `text_source` provenance. **Corrected from summary-first** after the test run below:
     generic CRS summaries strip political valence (a budget resolution enabling tax cuts read
     as a tax *increase*), so full text is the accurate default; summaries are only the
     oversized-bill fallback, where centrality + targeting already prevent over-attribution.
   - ✅ **Centrality rule** in the scoring prompt: magnitude must track how central a topic
     is to the *whole* bill, so a buried clause can't dominate. Prompt is now folded into the
     analysis cache key (a prompt change invalidates stale scores); `analyze_bill(force=)` added.
   - ✅ **Guardrail tests** (`tests/test_bill_scoring.py`, gemini-2.5-flash): NDAA now scores
     lgbtq_rights 0.0 (was +1.0), military_defense +1.0, government_spending −1.0; tax/gun/
     null-naming bills all in-band. 4/4 pass.
   - ✅ **`policyArea` prefilter + targeting** (`services/congress/policy_areas.py`, wired into
     s4): skips bills whose policy area maps to no topic (~17% — namings, internal congressional
     matters, finance/commerce we have no axis for), and scores only the relevant topics —
     **2.8 topics/bill vs 19** (~7× output-cost cut). Defense bills map to
     [military_defense, foreign_aid], so LGBTQ is never even asked (belt-and-suspenders on the
     Barry Moore class of bug). Unknown/missing areas fall back to all topics — never drop a
     bill. Pure mapping tests in `tests/test_policy_areas.py` (6/6 pass).
   - ✅ **Lean output**: the prompt now returns an entry ONLY for nonzero topics (omitted =
     0), with 1–2 sentence thoughts. NDAA dropped from 19 entries to 4; test runtime ~41s→14s.
     Scoring tests updated to treat omitted topics as 0.
   - ⏳ Remaining: flip `analyze_voted_only=False`, then run the full batch once the download
     completes. **Two-tier escalation is now optional** — summary-first (~23× input) +
     targeting (~7× fewer topics) + lean output make the full flash batch ~$1–3, so cost is no
     longer the driver; escalation would be purely for a quality second-opinion if desired.

**Note:** running the LLM scoring tests while the bill download is writing the cache can
transiently fail on a half-written JSON read (a race, not a logic bug) — re-run, or run tests
once the fetch is done.

### Phase 2 test run (voted bills only) — findings

Re-scored the ~198 voted bills with the new flash pipeline and diffed alignment vs the
post-Phase-1 baseline. Result was alarming on purpose-built diffing: **427/430 reps changed,
1,275 sign flips**. Two root causes, both important:

1. **Single-bill topic fragility (the real disease).** Many topics are driven by *one* bill
   across the entire House — taxation 421/421 reps, national_debt 421/421, voting_elections
   420/420, tech_privacy 403/403 are all n=1. The whole House's taxation alignment rests on
   one budget-resolution vote. This is the low-confidence problem (47%) at its extreme, and it
   is exactly what the sponsorship data fixes: more bills per topic → no single vote can swing
   it. **This is the strongest argument for finishing the sponsorship pipeline.**
2. **Summary-first regression (fixed).** The pivotal budget resolution (HCONRES 14) flipped
   taxation +0.9 → −0.5 under summary-first; full text scores it +0.7 (isolated: the new
   prompt is fine, the summary was the cause). → switched to full-text-first (above) and added
   HCONRES 14 as a regression test. Test output preserved in `output/s4_analysis_p2test` /
   `output/s5_alignment_p2test`; baseline restored as the live alignment.

Takeaway: do NOT judge alignment changes until topics have enough bills behind them. Run the
full batch (with full-text-first) only after the sponsorship download completes.
5. Sponsorship **stance weighting** — ✅ **built**. s5 now uses a unified *contribution*
   model: each bill a member engaged contributes once, at the weight of their strongest role
   (vote 1.0 > sponsor 0.8 > cosponsor 0.4; sponsorship is always an endorsement, +). Votes
   stay directional (Yea/+, Nay/−); sponsorship has no oppose. Confidence + `contributing_
   signal_count` now span votes + sponsorship, and contributing bills carry `role`.
   **Impact (with only 785/~5,800 sponsorship bills scored so far):** low-confidence topics
   47%→22%, high-confidence 6%→32%; taxation reps with n=1 went **421/421 → 63/429** — the
   single-bill fragility is largely gone, and improves further as more bills are scored.
   Refinements still open: lead/original-cosponsor weighting, identical-companion dedup.
6. **Impact dimension**: per-bill impact score (progress stage × coalition × sponsor power) +
   member-level effectiveness score; validate against the Legislative Effectiveness Score.
7. Recency guardrail (dormant until 118th Congress is added).
