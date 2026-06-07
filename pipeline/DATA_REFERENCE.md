# Govstalker Pipeline Data Reference

Govstalker tracks US House representatives across three dimensions: how they vote on legislation, where they stand on political topics (scored by LLM analysis of bill text), and who funds their campaigns. The pipeline collects data from Congress.gov and OpenFEC APIs, then aggregates everything into one JSON file per candidate for the frontend to consume.

Data lives in `data/pipeline/output/`. The primary frontend data source is **s5_alignment** (one file per candidate, ~60-70KB each). **s3_bills** provides deep-dive bill data if you need a bill detail page. Pydantic models for all types are in `data/pipeline/models.py`.

## Current Data

- **3 candidates**: Alexandria Ocasio-Cortez (NY-14), Marjorie Taylor Greene (GA-14), Mike Johnson (LA-4)
- **222 bills** with full detail
- **167 bills** with LLM topic analysis
- **119th Congress** (2025-2027), **2024 FEC cycle**

---

## s5_alignment — `PipelineCandidate` (primary frontend data)

**Path**: `pipeline/output/s5_alignment/{state}_{district}.json` (e.g., `NY_14.json`)

This is the main file per candidate. Everything the frontend needs is here.

```
PipelineCandidate
├── profile: CandidateProfile
│   ├── bioguide_id: str                    # "O000172"
│   ├── name: str                           # "Alexandria Ocasio-Cortez"
│   ├── state: str                          # "NY"
│   ├── district: int                       # 14
│   ├── party: str | null                   # "Democratic"
│   ├── birth_year: str | null              # "1989"
│   ├── website: str | null                 # "https://ocasio-cortez.house.gov/"
│   ├── serving_since: int | null           # 2019
│   ├── terms: Term[]
│   │   ├── chamber: str                    # "House of Representatives"
│   │   ├── congress: int                   # 116
│   │   ├── startYear / endYear: int
│   │   ├── stateCode: str                  # "NY"
│   │   └── stateName: str                  # "New York"
│   └── fec_id: str | null                  # "H8NY15148"
│
├── alignment: Alignment
│   ├── congress: int                       # 119
│   ├── votes_analyzed: int                 # number of votes with bill analysis
│   ├── votes_without_analysis: int
│   ├── topics: TopicAlignment[]            # sorted by |alignment| descending
│   │   ├── topic_slug: str                 # "immigration"
│   │   ├── topic_name: str                 # "Immigration"
│   │   ├── alignment: float               # -1.0 to 1.0 (the key score)
│   │   ├── numerator: float               # sum of scores for Yea votes
│   │   ├── denominator: float             # sum of |scores| for all votes
│   │   ├── minus_one_desc: str            # "Restrict immigration and enforce borders"
│   │   ├── plus_one_desc: str             # "Expand immigration pathways and protections"
│   │   └── contributing_bills: ContributingBill[]  # sorted by |impact| desc
│   │       ├── bill_type: str             # "HR"
│   │       ├── bill_number: str           # "22"
│   │       ├── title: str                 # "SAVE Act"
│   │       ├── url: str                   # congress.gov link
│   │       ├── vote_position: str         # "Yea", "Nay", "Not Voting", "Present"
│   │       ├── bill_topic_score: float    # how much this bill impacts this topic
│   │       ├── contributed_to_alignment: bool  # true if they voted Yea
│   │       └── vote_date: str | null      # "2025-04-10"
│   └── topics_without_signal: SilentTopic[]
│       ├── topic_slug: str
│       └── topic_name: str
│
├── voting_history: VoteRecord[]            # 50 most recent votes
│   ├── vote_number: int
│   ├── vote_date: str | null               # "2025-09-08"
│   ├── vote_question: str | null           # "2/3 Yea-And-Nay"
│   ├── vote_result: str | null             # "Passed", "Failed"
│   ├── member_position: str                # "Yea", "Nay", "Not Voting", "Present"
│   └── bill: VoteBillRef | null
│       ├── number: str                     # "3424"
│       ├── type: str                       # "HR"
│       ├── title: str | null               # "SPACE Act of 2025"
│       └── url: str | null                 # congress.gov link
│
├── sponsored_bills: BillSummaryRef[]       # bills they introduced
│   ├── congress: int
│   ├── number: str
│   ├── type: str                           # "HR", "HRES", "HJRES", "HCONRES"
│   ├── title: str | null
│   ├── introduced_date: str | null
│   ├── latest_action: { actionDate, text }
│   └── url: str | null                     # congress.gov link
│
├── cosponsored_bills: BillSummaryRef[]     # bills they co-signed
│   └── (same shape as sponsored_bills)
│
└── finance: CandidateFinance | null
    ├── fec_id: str
    ├── name: str
    ├── cycle: int                          # 2024
    ├── totals: FinanceTotals | null
    │   ├── receipts: float                 # total raised
    │   ├── disbursements: float            # total spent
    │   ├── cash_on_hand_end_period: float
    │   ├── individual_itemized_contributions: float
    │   ├── other_political_committee_contributions: float  # PAC money in
    │   ├── party: str | null               # "DEM", "REP"
    │   └── incumbent_challenge_full: str   # "Incumbent", "Challenger", "Open seat"
    ├── committees: FinanceCommittee[]
    │   ├── committee_id: str
    │   ├── name: str
    │   └── designation: str                # "P"=principal, "J"=joint, "A"=authorized
    ├── top_pac_contributions: PACContribution[]
    │   ├── contributor_id: str
    │   ├── contributor_name: str
    │   ├── total: float
    │   └── detail: CommitteeDetail | null
    │       ├── committee_type_full: str    # "Super PAC", "Traditional PAC", etc.
    │       ├── organization_type_full: str  # "Corporation", "Labor", etc.
    │       └── connected_organization_name: str | null
    ├── top_individual_contributions: IndividualContribution[]
    │   ├── contributor_name: str
    │   ├── total: float
    │   ├── occupation: str | null
    │   ├── employer: str | null
    │   └── state: str | null
    ├── contributions_by_employer: EmployerContribution[]
    │   ├── employer: str | null
    │   ├── total: float
    │   └── count: int                      # number of donors from this employer
    ├── independent_expenditures: IndependentExpenditure[]
    │   ├── committee_id: str
    │   ├── committee_name: str
    │   ├── support_oppose_indicator: str   # "S"=support, "O"=oppose
    │   ├── total: float
    │   └── count: int
    ├── electioneering: ElectioneeringTotal[]
    │   ├── committee_name: str
    │   ├── total: float                    # broadcast ad spend
    │   └── count: int
    └── committee_details: { [committee_id]: CommitteeDetail }
```

### Alignment Score Explained

`alignment` is a float from **-1.0 to +1.0** per topic. Each topic has `minus_one_desc` (what -1 means) and `plus_one_desc` (what +1 means). For example, Immigration: -1.0 = "More immigration", +1.0 = "Secure Borders".

The score is computed from voting records: a **Yea** adds the bill's topic score and a **Nay** subtracts it, over the total stake of bills the member took a position on — `numerator = Σ(+score for Yea, −score for Nay)`, `denominator = Σ|score|`, `alignment = numerator / denominator`. Present / Not-Voting are excluded (an abstention isn't a for/against signal). The `contributing_bills` array shows exactly which bills drove that score.

### 19 Topic Slugs

`government_spending`, `taxation`, `healthcare`, `gun_control`, `immigration`, `abortion`, `military_defense`, `climate_environment`, `social_safety_net`, `education`, `drug_policy`, `criminal_justice`, `trade_policy`, `national_debt`, `lgbtq_rights`, `foreign_aid`, `voting_elections`, `tech_privacy`, `labor_unions`

---

## s3_bills — `PipelineBill` (bill detail pages)

**Path**: `pipeline/output/s3_bills/{congress}_{type}_{number}.json` (e.g., `119_HR_22.json`)

Use this if you want a dedicated bill detail page. 222 bills available.

```
PipelineBill
├── congress: int
├── bill_type: str                          # "HR", "HCONRES", etc.
├── bill_number: str
├── detail: BillDetail
│   ├── title: str | null
│   ├── introduced_date: str | null
│   ├── origin_chamber: str                 # "House"
│   ├── policy_area: str | null             # "Immigration"
│   ├── sponsors: BillSponsor[]
│   │   ├── fullName: str                   # "Rep. Roy, Chip [R-TX-21]"
│   │   ├── party: str                      # "R"
│   │   └── state: str
│   ├── latest_action_date: str
│   ├── latest_action_text: str
│   ├── actions: BillAction[]               # chronological legislative actions
│   └── summaries: BillSummaryText[]        # CRS summaries (may contain HTML)
├── amendments: BillAmendment[]
├── committees: BillCommittee[]
├── cosponsors: BillCosponsor[]             # who co-signed, with party/state
├── related_bills: BillRelatedBill[]
├── subjects: BillSubject[]                 # legislative subject tags
├── titles: BillTitle[]                     # short/official/display titles
├── text_versions: BillTextVersion[]
│   └── formats: BillTextFormat[]
│       ├── type: str                       # "Formatted Text", "PDF", "Formatted XML"
│       └── url: str                        # direct link to text on congress.gov
└── has_xml: bool                           # whether full XML text was fetched
```

---

## File Layout

```
data/pipeline/
├── candidates.json              # candidate registry (add new candidates here)
├── config.json                  # congress=119, session=1, cycle=2024, limits
├── models.py                    # Pydantic types for all output data
├── run.py                       # CLI: python pipeline/run.py [stages...] [--force]
├── stages/
│   ├── s1_members.py            # fetch congressional profiles + votes
│   ├── s2_finance.py            # fetch campaign finance from OpenFEC
│   ├── s3_bills.py              # deep-dive bill details
│   ├── s4_analysis.py           # LLM topic scoring (parallel, 10 at a time)
│   └── s5_alignment.py          # aggregate everything into final candidate files
└── output/
    ├── s1_members/              # intermediate: raw member data
    ├── s2_finance/              # intermediate: raw finance data
    ├── s3_bills/                # 222 bill detail files (PipelineBill)
    ├── s4_analysis/             # 167 LLM topic score files
    └── s5_alignment/            # 3 candidate files (PipelineCandidate) ← MAIN OUTPUT
```
