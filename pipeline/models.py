"""Pydantic models for pipeline output data (s3_bills and s5_alignment)."""

from pydantic import BaseModel


# ── s3_bills output ──────────────────────────────────────────────────────────


class BillSponsor(BaseModel):
    bioguideId: str | None = None
    district: int | None = None
    firstName: str | None = None
    fullName: str | None = None
    lastName: str | None = None
    party: str | None = None
    state: str | None = None


class BillAction(BaseModel):
    action_date: str | None = None
    text: str | None = None
    type: str | None = None


class BillSummaryText(BaseModel):
    action_date: str | None = None
    action_desc: str | None = None
    text: str | None = None
    update_date: str | None = None


class BillDetail(BaseModel):
    congress: int
    number: str
    type: str
    title: str | None = None
    introduced_date: str | None = None
    origin_chamber: str | None = None
    policy_area: str | None = None
    sponsors: list[BillSponsor] = []
    latest_action_date: str | None = None
    latest_action_text: str | None = None
    actions: list[BillAction] = []
    summaries: list[BillSummaryText] = []


class BillAmendment(BaseModel):
    number: str | None = None
    type: str | None = None
    congress: int | None = None
    description: str | None = None
    latest_action: dict | None = None
    purpose: str | None = None
    url: str | None = None


class BillCommittee(BaseModel):
    name: str | None = None
    chamber: str | None = None
    type: str | None = None
    system_code: str | None = None
    url: str | None = None
    activities: list[dict] | None = None


class BillCosponsor(BaseModel):
    bioguide_id: str
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    party: str | None = None
    state: str | None = None
    district: int | None = None
    sponsorship_date: str | None = None
    is_original_cosponsor: bool | None = None


class BillRelatedBill(BaseModel):
    congress: int | None = None
    number: int | None = None
    type: str | None = None
    title: str | None = None
    relationship_details: list[dict] | None = None
    latest_action: dict | None = None


class BillSubject(BaseModel):
    name: str | None = None
    update_date: str | None = None


class BillTitle(BaseModel):
    title: str | None = None
    title_type: str | None = None
    title_type_code: int | None = None
    chamber: str | None = None
    bill_text_version_name: str | None = None
    bill_text_version_code: str | None = None


class BillTextFormat(BaseModel):
    type: str | None = None
    url: str | None = None


class BillTextVersion(BaseModel):
    date: str | None = None
    type: str | None = None
    url: str | None = None
    formats: list[BillTextFormat] = []


class PipelineBill(BaseModel):
    """Full bill output from stage 3 (s3_bills)."""
    congress: int
    bill_type: str
    bill_number: str
    detail: BillDetail
    amendments: list[BillAmendment] = []
    committees: list[BillCommittee] = []
    cosponsors: list[BillCosponsor] = []
    related_bills: list[BillRelatedBill] = []
    subjects: list[BillSubject] = []
    titles: list[BillTitle] = []
    text_versions: list[BillTextVersion] = []
    has_xml: bool = False


# ── s5_alignment output ──────────────────────────────────────────────────────


class Term(BaseModel):
    chamber: str | None = None
    congress: int | None = None
    district: int | None = None
    endYear: int | None = None
    memberType: str | None = None
    startYear: int | None = None
    stateCode: str | None = None
    stateName: str | None = None


class CandidateProfile(BaseModel):
    bioguide_id: str
    name: str
    state: str
    district: int
    party: str | None = None
    birth_year: str | None = None
    website: str | None = None
    serving_since: int | None = None
    terms: list[Term] = []
    fec_id: str | None = None


class ContributingBill(BaseModel):
    """A bill that contributed to a candidate's alignment on a topic."""
    bill_type: str
    bill_number: str
    vote_position: str
    bill_topic_score: float
    contributed_to_alignment: bool
    vote_date: str | None = None
    # title/summary/topic_scores fetched lazily by id; url derived client-side


class TopicAlignment(BaseModel):
    """A candidate's alignment on a single political topic."""
    topic_slug: str
    topic_name: str
    alignment: float          # -1.0 to 1.0
    numerator: float
    denominator: float
    minus_one_desc: str | None = None
    plus_one_desc: str | None = None
    contributing_bills: list[ContributingBill] = []


class SilentTopic(BaseModel):
    topic_slug: str
    topic_name: str


class Alignment(BaseModel):
    congress: int
    votes_analyzed: int
    votes_without_analysis: int
    topics: list[TopicAlignment] = []
    topics_without_signal: list[SilentTopic] = []


class VoteBillRef(BaseModel):
    number: str
    type: str
    # title fetched lazily by id; url derived client-side


class VoteRecord(BaseModel):
    vote_number: int
    vote_date: str | None = None
    vote_question: str | None = None
    vote_result: str | None = None
    member_position: str
    bill: VoteBillRef | None = None


class BillTopicScoreRef(BaseModel):
    """A single topic score for a bill (for display in bill cards)."""
    topic_slug: str
    topic_name: str
    score: float


class BillSummaryRef(BaseModel):
    """Lightweight bill reference from sponsored/cosponsored lists. Only the id +
    introduced date ship inline; title/summary/topic_scores are fetched lazily by id
    and the url is derived client-side."""
    congress: int
    number: str
    type: str
    introduced_date: str | None = None


# ── Finance models ───────────────────────────────────────────────────────────


class FinanceTotals(BaseModel):
    candidate_id: str
    name: str
    party: str | None = None
    office: str | None = None
    state: str | None = None
    cycle: int
    incumbent_challenge_full: str | None = None
    receipts: float = 0.0
    disbursements: float = 0.0
    cash_on_hand_end_period: float = 0.0
    individual_itemized_contributions: float = 0.0
    other_political_committee_contributions: float = 0.0


class FinanceCommittee(BaseModel):
    committee_id: str
    name: str
    designation: str | None = None
    committee_type: str | None = None


class CommitteeDetail(BaseModel):
    committee_id: str
    name: str
    committee_type_full: str | None = None
    organization_type_full: str | None = None
    connected_organization_name: str | None = None
    party: str | None = None
    state: str | None = None


class PACRecipient(BaseModel):
    recipient_id: str | None = None
    recipient_name: str | None = None
    total: float = 0.0
    party: str | None = None        # "DEM", "REP", or None if unclassifiable


class FundingSizeBucket(BaseModel):
    label: str                      # "Under $200", "$2,000+", ...
    total: float = 0.0
    count: int = 0


class PACLean(BaseModel):
    """Rules-based partisan lean of a PAC, derived from the party split of its top
    recipients (party/leadership committees and candidate committees resolve to a party;
    PAC-to-PAC transfers are unclassified)."""
    label: str                      # "Leans Democratic", "Bipartisan", "Republican", "Unclassified"
    dem_total: float = 0.0
    rep_total: float = 0.0
    other_total: float = 0.0
    basis: str | None = None        # e.g. "top 8 recipients"


class PACProfile(BaseModel):
    """Standalone profile of a PAC — who it funds, who funds it, its size and lean.

    Keyed by committee_id and emitted as its own file so it can back both the inline
    expandable card and a future dedicated /pac/:id page, shared across candidates.
    """
    committee_id: str
    name: str | None = None
    committee_type_full: str | None = None
    organization_type_full: str | None = None
    connected_organization_name: str | None = None
    cycle: int
    receipts: float = 0.0
    disbursements: float = 0.0
    cash_on_hand: float = 0.0
    lean: PACLean | None = None
    top_recipients: list[PACRecipient] = []      # who it funds
    funding_by_size: list[FundingSizeBucket] = []  # who funds it (by donation size)


class PACContribution(BaseModel):
    contributor_id: str
    contributor_name: str
    total: float
    detail: CommitteeDetail | None = None


class IndividualContribution(BaseModel):
    contributor_name: str
    total: float
    occupation: str | None = None
    employer: str | None = None
    state: str | None = None


class EmployerContribution(BaseModel):
    employer: str | None = None
    total: float = 0.0
    count: int = 0


class IndependentExpenditure(BaseModel):
    committee_id: str | None = None
    committee_name: str | None = None
    support_oppose_indicator: str | None = None  # "S" or "O"
    total: float = 0.0
    count: int = 0


class ElectioneeringTotal(BaseModel):
    committee_id: str | None = None
    committee_name: str | None = None
    total: float = 0.0
    count: int = 0


class FundingComponent(BaseModel):
    key: str            # "individual_small", "pac", ...
    label: str          # "Small-dollar individuals"
    amount: float


class FundingBreakdown(BaseModel):
    """Receipts decomposed into labeled buckets that sum back to `total`.

    Replaces the frontend's old receipts − itemized − pac = "Other" computation,
    which dumped the majority of small-dollar-funded campaigns into a gray bucket.
    """
    source: str = "committee_totals"
    total: float = 0.0
    components: list[FundingComponent] = []
    unaccounted: float = 0.0    # residual; should be ~0


class CandidateFinance(BaseModel):
    """Full finance output from stage 2 (s2_finance)."""
    fec_id: str
    name: str
    cycle: int
    fetched_at: str | None = None              # ISO date the data was pulled
    data_quality_warnings: list[str] = []
    totals: FinanceTotals | None = None
    funding_breakdown: FundingBreakdown | None = None
    committees: list[FinanceCommittee] = []
    top_pac_contributions: list[PACContribution] = []
    top_individual_contributions: list[IndividualContribution] = []
    contributions_by_employer: list[EmployerContribution] = []
    independent_expenditures: list[IndependentExpenditure] = []
    electioneering: list[ElectioneeringTotal] = []
    committee_details: dict[str, CommitteeDetail] = {}
    pac_profiles: dict[str, PACProfile] = {}    # committee_id -> profile, for donor PACs & outside spenders


# ── Top-level aggregated candidate (s5_alignment output) ─────────────────────


class PipelineCandidate(BaseModel):
    """Complete aggregated candidate data from stage 5 (s5_alignment).

    This is the main type for the frontend to consume — one file per candidate
    containing profile, alignment with bill breakdowns, voting history,
    legislation lists, and campaign finance data.
    """
    profile: CandidateProfile
    alignment: Alignment
    voting_history: list[VoteRecord] = []
    sponsored_bills: list[BillSummaryRef] = []
    cosponsored_bills: list[BillSummaryRef] = []
    finance: CandidateFinance | None = None
