# WatchGov Pipeline Data Reference

WatchGov tracks U.S. House representatives across three dimensions: how they vote on and sponsor legislation, where they stand on 5 policy topics (scored by LLM analysis of bill text), and who funds their campaigns. The pipeline collects data from the Congress.gov and OpenFEC APIs, scores bills with an LLM, and aggregates everything into one JSON file per member for downstream consumers.

Data lives in [`pipeline/output/`](output/). The primary data source is **`s5_alignment`** (one self-contained file per member). **`s3_bills`** provides deep-dive detail for individual bills. Pydantic models for the intermediate types are in [`pipeline/models.py`](models.py); note that `s5_alignment` is assembled as a plain dict in [`stages/s5_alignment.py`](stages/s5_alignment.py), so *this document is the authoritative schema for that file*.

## Current dataset (snapshot)

- **431 members** — the full U.S. House (one file per seat; at-large seats use district `0`)
- **119th Congress** (2025–2026), **2024 FEC cycle**
- **~10,700 bill detail files** (`s3_bills`), of which **~3,500 are LLM-analyzed** (`s4_analysis` — only floor-voted bills are scored by default; see `analyze_voted_only` in [config.json](config.json))

---

## s5_alignment — primary per-member data

**Path**: `pipeline/output/s5_alignment/{STATE}_{DISTRICT}.json` (e.g. `NY_14.json`, `AK_0.json`)

Everything a consumer needs for one member is in this file. Bill titles/URLs are intentionally **not** inlined on the alignment/voting/sponsorship lists — only the bill id (`type` + `number`) ships, and the title is fetched lazily from `s3_bills` while the congress.gov URL is derived client-side.

```
{
  profile: {
    bioguide_id: str                # "O000172"
    name: str                       # "Alexandria Ocasio-Cortez"
    state: str                      # "NY"
    district: int                   # 14  (at-large = 0)
    party: str | null               # "Democratic"
    birth_year: str | null          # "1989"
    website: str | null
    serving_since: int | null       # 2019
    terms: [ { chamber, congress, memberType, startYear, stateCode, stateName } ]
    fec_id: str | null              # "H8NY15148"
  },

  alignment: {
    congress: int                   # 119
    votes_analyzed: int             # bills with analysis that contributed a signal
    votes_without_analysis: int     # engaged bills that had no LLM analysis on disk
    topics: [ TopicAlignment ]      # sorted by |alignment| descending
    topics_without_signal: [ { topic_slug, topic_name } ]   # topics with no evidence
  },

  voting_history: [                 # every recorded vote, most recent first
    {
      session: int                  # 1 = 2025, 2 = 2026
      vote_number: int
      vote_date: str | null
      vote_question: str | null     # "On Passage", "On Motion to Recommit", ...
      vote_result: str | null       # "Passed", "Failed"
      member_position: str          # "Yea" | "Nay" | "Aye" | "No" | "Present" | "Not Voting"
      bill: { number, type } | null # id only; title/url resolved from s3_bills
    }
  ],

  sponsored_bills:   [ { congress, number, type, introduced_date } ],
  cosponsored_bills: [ { congress, number, type, introduced_date } ],

  finance: CandidateFinance | null  # see "finance" below
}
```

### TopicAlignment

```
{
  topic_slug: str                   # "immigration"
  topic_name: str                   # "Immigration"
  alignment: float                  # -1.0..1.0 — THE DISPLAYED SCORE (shrunk, see below)
  raw_alignment: float              # -1.0..1.0 — lean before shrinkage (transparency)
  numerator: float                  # Σ(weight · sign · score)
  denominator: float                # Σ(weight · |score|) — the topic's "evidence mass"
  salience: float                   # 0..1 — this topic's share of the member's total evidence
  contributing_signal_count: int    # # of votes + sponsorship signals behind the score
  confidence: str                   # "low" (<3) | "medium" (3–7) | "high" (≥8) signals
  minus_one_desc: str               # what -1 means, e.g. "More immigration"
  plus_one_desc: str                # what +1 means, e.g. "Secure Borders"
  contributing_bills: [ ContributingBill ]   # sorted by |bill_topic_score| desc
}
```

### ContributingBill

```
{
  bill_type: str                    # "HR"
  bill_number: str                  # "22"
  role: str                         # "vote" | "sponsor" | "cosponsor"
  weight: float                     # 1.0 vote · 0.8 sponsor · 0.4 cosponsor
  vote_position: str | null         # set only when role == "vote"
  vote_class: str | null            # "passage" | "amendment" | "procedural" | null
  bill_topic_score: float           # -1.0..1.0 — the LLM's score for this bill on this topic
  contributed_to_alignment: bool    # always true (only contributing signals are listed)
  vote_date: str | null
}
```

### How the alignment score is computed

A member's stance on a topic is an **evidence-weighted estimate with a confidence**, not a raw average of recent votes. Each bill the member engaged contributes **one** signal, at the weight of their strongest role on it (a vote outranks sponsorship, which outranks cosponsorship):

| Role | Weight | Direction |
|---|---|---|
| Vote on final passage | `1.0` | directional — Yea/Aye `+`, Nay/No `−` |
| Sponsored the bill | `0.8` | always `+` (authoring is endorsement) |
| Cosponsored | `0.4` | always `+` |

Procedural and amendment votes carry **no** weight and are excluded (party-discipline noise, not stance — see [`services/congress/vote_questions.py`](../services/congress/vote_questions.py)). Recognizing both `Yea/Nay` (final passage) and `Aye/No` (Committee of the Whole) is deliberate — missing the latter caused a real sign-flip bug (see [`ALIGNMENT_QUALITY_PLAN.md`](../ALIGNMENT_QUALITY_PLAN.md)).

For each topic the LLM assigns every bill a `score` in `[-1, +1]`, then:

```
numerator   = Σ (weight · sign · score)
denominator = Σ (weight · |score|)                 # "evidence mass" M
raw_alignment = numerator / denominator
alignment     = numerator / (denominator + K)      # K = 1.0  (evidence shrinkage)
salience      = denominator / Σ(all topics' denominator)
```

`K` (evidence shrinkage) pulls thinly-evidenced topics toward neutral, so a member who barely engages a topic can't show a full-strength bar; well-evidenced topics are ~unaffected. Topics are sorted by `|alignment|` so the strongest, best-evidenced stances lead.

Every topic has its own `minus_one_desc` / `plus_one_desc` poles. By convention **−1 = left-leaning, +1 = right-leaning** (e.g. taxation: −1 "Higher taxes" / +1 "Lower taxes"). Poles are defined in [`services/congress/topics.py`](../services/congress/topics.py).

### The 5 topic slugs

`military_defense`, `taxation`, `government_spending`, `trade_policy`, `foreign_aid`

The v1 topic set is deliberately narrow. `government_spending` absorbs national debt (a bill's `national_debt` score folds into it via `TOPIC_ALIASES` in [`stages/s5_alignment.py`](stages/s5_alignment.py)). Older `s4_analysis` files still carry scores for previously-tracked topics; `s5` simply ignores any slug not in the current set, so widening the set later is a one-line change in `topics.py`.

---

## finance — `CandidateFinance`

Embedded as `s5_alignment.finance` (and produced by the `finance` stage into `s2_finance/{fec_id}.json`). See [`FUNDRAISING_DATA_PLAN.md`](../FUNDRAISING_DATA_PLAN.md) for the collection methodology and known limitations.

```
{
  fec_id: str
  name: str
  cycle: int                        # 2024
  fetched_at: str                   # ISO timestamp of the fetch (freshness stamp)
  data_quality_warnings: [ str ]    # e.g. "PAC dollars in totals but 0 PAC contributors"

  totals: {                         # from candidate/{id}/totals
    receipts, disbursements, cash_on_hand_end_period: float
    individual_itemized_contributions, other_political_committee_contributions: float
    incumbent_challenge_full: str   # "Incumbent" | "Challenger" | "Open seat"
    party, office, state: str
  },

  funding_breakdown: {              # from committee/{id}/totals — replaces the old "Other" bucket
    source: str                     # which endpoint the breakdown came from
    total: float
    components: [ { key, label, amount } ]   # small-dollar, large individual, PAC, party, transfers, self-funding, ...
    unaccounted: float              # asserted < 1% of receipts
  },

  committees: [ { committee_id, name, designation, committee_type } ],

  top_pac_contributions: [ {
    contributor_id, contributor_name: str, total: float,
    detail: CommitteeDetail | null
  } ],
  top_individual_contributions: [ { contributor_name, total, occupation, employer, state } ],
  contributions_by_employer:      [ { employer, total, count } ],
  independent_expenditures:       [ { committee_id, committee_name, support_oppose_indicator, total, count } ],  # "S"/"O"
  electioneering:                 [ { committee_name, total, count } ],

  committee_details: { [committee_id]: CommitteeDetail },
  pac_profiles:      { [committee_id]: PACProfile }
}

CommitteeDetail = {
  committee_id, name: str, committee_type_full: str,
  organization_type_full, connected_organization_name, party: str | null, state: str | null
}

PACProfile = {                      # who a PAC funds and how it's funded (see FUNDRAISING_DATA_PLAN.md)
  committee_id, name, committee_type_full: str
  organization_type_full, connected_organization_name: str | null
  cycle: int
  receipts, disbursements, cash_on_hand: float
  lean: { label, basis: str, dem_total, rep_total, other_total: float }   # rules-based partisan lean
  top_recipients:  [ { recipient_id, recipient_name, total, party } ]     # who it funds
  funding_by_size: [ { label, total, count } ]                            # how it's funded (donation bands)
}
```

---

## s3_bills — `PipelineBill` (bill detail)

**Path**: `pipeline/output/s3_bills/{congress}_{TYPE}_{number}.json` (e.g. `119_HR_22.json`)

Use this to resolve the bill titles/URLs referenced by `s5_alignment`, or to build a bill detail page.

```
{
  congress: int
  bill_type: str                    # "HR", "HRES", "HJRES", "HCONRES", "S", ...
  bill_number: str
  detail: {
    title, introduced_date, origin_chamber, policy_area: str | null
    sponsors: [ { fullName, party, state } ]
    latest_action_date, latest_action_text: str
    actions: [ ... ]                # chronological legislative actions
    summaries: [ ... ]              # CRS summaries (may contain HTML)
  }
  amendments, committees, cosponsors, related_bills: [ ... ]
  subjects:      [ { name, update_date } ]
  titles:        [ { title, title_type, title_type_code, ... } ]
  text_versions: [ { date, type, url, formats: [ { type, url } ] } ]
  has_xml: bool                     # whether full bill XML was fetched
}
```

## s4_analysis — LLM topic scores (intermediate)

**Path**: `pipeline/output/s4_analysis/{congress}_{TYPE}_{number}.json`

The per-bill LLM output that `s5_alignment` aggregates. Files record the model and temperature that produced them, so every score is traceable (the shipped 119th-Congress batch predates this stamp — it was scored with `x-ai/grok-4.3` at temperature 0; files written from now on carry the fields inline).

```
{
  congress: int, bill_type: str, bill_number: str
  summary: str                      # short plain-language summary
  text_source: str                  # "full_text" | "summary" | "truncated_full_text"
  scores: [ { topic_slug, score } ] # score in -1.0..1.0; omitted topics are implicitly 0
  llm_model: str                    # e.g. "x-ai/grok-4.3"
  temperature: float                # 0.0
}
```

---

## File layout

```
pipeline/
├── candidates.json              # member registry (generated by scripts/build_roster.py)
├── config.json                  # congress=119, cycle=2024, llm_model, limits, parallelism
├── models.py                    # Pydantic types for intermediate data
├── run.py                       # CLI: python pipeline/run.py [stages...] [--force]
├── stages/
│   ├── s1_members.py            # profiles, votes, sponsored/cosponsored bills
│   ├── s2_finance.py            # campaign finance from OpenFEC (breakdown, PACs, outside spending)
│   ├── s3_bills.py              # deep-dive bill detail
│   ├── s4_analysis.py           # LLM topic scoring (parallel)
│   └── s5_alignment.py          # aggregate votes + sponsorship + analysis → per-member files
└── output/
    ├── s1_members/              # intermediate: raw member data
    ├── s2_finance/              # intermediate: raw finance data (+ pac_profiles/)
    ├── s3_bills/                # bill detail files (PipelineBill)
    ├── s4_analysis/             # per-bill LLM topic scores
    └── s5_alignment/            # per-member files ← PRIMARY OUTPUT
```
