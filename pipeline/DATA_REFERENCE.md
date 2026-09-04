# WatchGov Pipeline Data Reference

WatchGov tracks U.S. House representatives across three dimensions: how they vote on and sponsor legislation, where they stand on 7 policy topics (scored by LLM analysis of bill text), how much they cross party lines, and who funds their campaigns. The pipeline collects data from the Congress.gov and OpenFEC APIs, scores bills with an LLM, and aggregates everything into one JSON file per member for downstream consumers.

Data lives in [`pipeline/output/`](output/). The primary data source is **`s5_alignment`** (one self-contained file per member). **`s3_bills`** provides deep-dive detail for individual bills. Pydantic models for the intermediate types are in [`pipeline/models.py`](models.py); note that `s5_alignment` is assembled as a plain dict in [`stages/s5_alignment.py`](stages/s5_alignment.py), so *this document is the authoritative schema for that file*.

## Current dataset (snapshot)

- **431 members** — the full U.S. House (one file per seat; at-large seats use district `0`)
- **119th Congress** (2025–2026), **2024 FEC cycle**
- **~10,700 bill detail files** (`s3_bills`), of which **~3,500 are LLM-analyzed** (`s4_analysis` — only floor-voted bills are scored by default; see `analyze_voted_only` in [config.json](config.json))
- **Cross-party rates for all 431 members** (`s7_bipartisanship`), computed from the roll-call and sponsorship record with no LLM involved

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
    truth_score: {                  # stated positions (s6) vs voting record — see below
      score: int | null             # 0..100, "words match votes" (null if <1 comparable topic)
      topics_compared: int          # high-confidence topics with a stated position, behind the score
      fetched_at: str | null        # when the member's site was last scraped
      note: str
    }
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

  bipartisanship: Bipartisanship | null   # see "bipartisanship" below
  finance: CandidateFinance | null  # see "finance" below
}
```

### TopicAlignment

```
{
  topic_slug: str                   # "budget_deficit"
  topic_name: str                   # "Budget Deficit"
  alignment: float                  # -1.0..1.0 — THE DISPLAYED SCORE (shrunk, see below)
  raw_alignment: float              # -1.0..1.0 — lean before shrinkage (transparency)
  numerator: float                  # Σ(weight · sign · score)
  denominator: float                # Σ(weight · |score|) — the topic's "evidence mass"
  salience: float                   # 0..1 — this topic's share of the member's total evidence
  contributing_signal_count: int    # # of votes + sponsorship signals behind the score
  confidence: str                   # "low" (<3) | "medium" (3–7) | "high" (≥8) signals
  minus_one_desc: str               # what -1 means, e.g. "More immigration"
  plus_one_desc: str                # what +1 means, e.g. "Secure Borders"
  stated: {                         # the member's STATED position on this topic (s6), or null
    score: float                    # -1..+1 on the same axis as `alignment` (the "says" bar)
    quote: str | null              # verbatim supporting quote from their website
    reasoning: str | null
    source: str | null             # the member's site the statement came from
  } | null
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

### The 7 topic slugs

`military_defense`, `taxation`, `budget_deficit`, `trade_policy`, `foreign_aid`, `healthcare_affordability`, `money_in_politics`

The topic set is deliberately narrow. `budget_deficit` **replaced** the earlier `government_spending` axis (which had itself absorbed `national_debt`): the axis is now deficit tolerance, so a fully paid-for spending increase is no longer a −1. Those retired scores are **not** aliased forward — the model reasoned about spending *levels*, so reusing the numbers under a deficit label would claim a judgement it never made. `TOPIC_ALIASES` in [`stages/s5_alignment.py`](stages/s5_alignment.py) is empty for that reason; add an entry only when a rename genuinely preserves an axis's meaning.

Older `s4_analysis` files still carry scores for previously-tracked topics; `s5` ignores any slug not in the current set. Adding a topic is a one-line change in `topics.py` plus a [`policy_areas.py`](../services/congress/policy_areas.py) mapping entry — `s4` then scores only the *missing* topics on only the *relevant* bills (`python pipeline/run.py analysis --topup-only`).

---

## bipartisanship — how much a member crosses party lines

**Path**: `pipeline/output/s7_bipartisanship/{STATE}_{DISTRICT}.json`, copied into `s5_alignment` under `bipartisanship`.

Deliberately **not** a topic. The alignment topics are LLM-scored −1..+1 left/right axes; these are plain rates computed from the roll-call and sponsorship record with no model in the loop. They get their own block (and their own UI section, alongside finance rather than among the alignment bars), and they never feed the truth score — there is no stated-stance counterpart to compare them against.

```
{
  state, district, name, bioguide_id, party
  vote_defection:  float | null     # 0..1 — substantive votes cast against own party's majority
  cosponsor_reach: float | null     # 0..1 — cosponsorships given to the other party's bills
  attracted_reach: float | null     # 0..1 — own bills that drew a cross-party cosponsor
  composite:       float | null     # mean of whichever rates cleared the evidence threshold
  rank:       int | null            # 1 = most bipartisan
  percentile: int | null            # 0..100 — share of the other scored members ranked above
  ranked_against: int               # members with enough record to score
  signals: {                        # the raw counts behind every rate, so each is checkable
    votes_counted, defections,
    cosponsored_counted, cosponsored_cross_party,
    sponsored_counted, sponsored_with_cross_party_support
  }
  defection_examples: [ { session, vote_number, vote_date, bill, member_position,
                          party_majority_position } ]    # up to 10, the receipts
  note: str, caveats: [ str ]
}
```

**How it's built.** A roll call's party majority comes from the cached `/members` payloads — the only place carrying every member's position *and* party. Procedural and amendment votes are excluded via `vote_weight()`, matching the alignment scoring. A rate is `null` below 5 signals rather than published thin, and independents get a `null` defection rate (no caucus majority to defect from).

**Read the percentile, not the composite.** The three rates have very different natural scales — the median member defects on 2.5% of votes but gives 16% of cosponsorships across the aisle — so the composite's absolute value means nothing on its own. The percentile ranks it against the whole House, both parties together: a within-party rank would hide the real fact that the parties cross over at different rates.

**Caveats that ship with the data.** `cosponsor_reach` is measured over the most recent 50 cosponsorships (the Congress.gov per-member list is capped, and 420 of 431 members are saturated at it), so it is a recent-record sample, not a full-term rate. `attracted_reach` counts only sponsored bills whose cosponsor list was actually fetched.

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
  scores: [ BillTopicScore ]
  llm_model: str                    # e.g. "x-ai/grok-4.3" — the MOST RECENT run to touch the file
  temperature: float                # 0.0
}

BillTopicScore {
  topic_slug: str, topic_name: str
  score: float                      # -1.0..1.0
  thoughts: str                     # the model's reasoning, written before the score
  llm_model: str                    # per-score, since a file can span several runs
  temperature: float
  scored_at: str                    # ISO timestamp of the run that produced THIS score
}
```

**Scoring is incremental by topic.** A bill is done only when its file holds a score for every topic its policy area targets, so adding a topic costs one call per relevant bill scoring only the missing topics, merged into the existing file. That is why provenance is stamped per score rather than only per file.

The scoring prompt asks the model to *omit* any topic it scores 0. Those omissions are persisted as explicit `0.0` entries (`thoughts` says so) — otherwise "absent" would mean both "never asked" and "asked, scored zero", and the bill would be re-queued and re-billed on every future run.

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
│   ├── s4_analysis.py           # LLM topic scoring (parallel, incremental by topic)
│   ├── s6_stances.py            # scrape member sites → LLM-score STATED positions (see services/positions/)
│   ├── s7_bipartisanship.py     # cross-party voting/cosponsorship rates (statistical, no LLM)
│   └── s5_alignment.py          # aggregate votes + sponsorship + analysis + stances + bipartisanship
└── output/
    ├── s1_members/              # intermediate: raw member data
    ├── s2_finance/              # intermediate: raw finance data (+ pac_profiles/)
    ├── s3_bills/                # bill detail files (PipelineBill)
    ├── s4_analysis/             # per-bill LLM topic scores
    ├── s6_stances/              # per-member stated positions (feeds the truth score)
    └── s5_alignment/            # per-member files ← PRIMARY OUTPUT
```

The **truth score** pairs each member's *stated* positions (scraped from their official site
in the `stances` stage, `s6`) against their *voted* alignment. It's an agreement measure over
high-confidence topics, gated so a thin or protest-vote record can't read as dishonesty. v1
coverage on the fiscal topic set is partial; see [`services/positions/NOTES.md`](../services/positions/NOTES.md).
