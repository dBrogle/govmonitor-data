from pydantic import BaseModel, Field


class CandidateTotals(BaseModel):
    model_config = {"extra": "ignore"}

    candidate_id: str
    name: str
    party: str | None = None
    office: str | None = None           # H, S, P
    state: str | None = None
    cycle: int
    incumbent_challenge_full: str | None = None  # "Incumbent", "Challenger", "Open seat"

    receipts: float = 0.0
    disbursements: float = 0.0
    cash_on_hand_end_period: float = 0.0
    individual_itemized_contributions: float = 0.0
    other_political_committee_contributions: float = 0.0   # PAC money


class Committee(BaseModel):
    model_config = {"extra": "ignore"}

    committee_id: str
    name: str
    designation: str | None = None      # P=principal, J=joint, A=authorized
    committee_type: str | None = None


class CommitteeTotals(BaseModel):
    """Full receipt breakdown for a committee (committee/{id}/totals/).

    Unlike candidates/totals/, this endpoint exposes every receipt category,
    most importantly individual_unitemized_contributions (small-dollar donors),
    which is what otherwise disappears into an unexplained "Other" bucket.
    The component fields below sum to `receipts`.
    """
    model_config = {"extra": "ignore"}

    committee_id: str
    cycle: int

    receipts: float = 0.0
    disbursements: float = 0.0
    last_cash_on_hand_end_period: float = 0.0

    individual_itemized_contributions: float = 0.0
    individual_unitemized_contributions: float = 0.0          # small-dollar (<$200)
    other_political_committee_contributions: float = 0.0      # PAC money
    political_party_committee_contributions: float = 0.0      # party money
    transfers_from_other_authorized_committee: float = 0.0    # JFC / authorized transfers
    candidate_contribution: float = 0.0                       # candidate self-funding
    offsets_to_operating_expenditures: float = 0.0
    other_receipts: float = 0.0


class Contribution(BaseModel):
    """Single itemized receipt (Schedule A)."""
    model_config = {"extra": "ignore", "populate_by_name": True}

    contributor_name: str | None = None
    contributor_id: str | None = None   # FEC committee ID if entity_type is COM/PAC
    contributor_employer: str | None = None
    contributor_occupation: str | None = None
    entity_type: str | None = None      # IND, COM, PAC, PTY, CCM
    is_individual: bool = False
    contributor_state: str | None = None
    amount: float = Field(0.0, alias="contribution_receipt_amount")
    date: str | None = Field(None, alias="contribution_receipt_date")
    line_number: str | None = None        # 11AI/11AII=individual, 11B=party, 11C=PAC, 12=transfers


class CommitteeDetail(BaseModel):
    """PAC/committee identity — what org is behind the money."""
    model_config = {"extra": "ignore"}

    committee_id: str
    name: str
    committee_type_full: str | None = None      # "Super PAC", "Traditional PAC", etc.
    organization_type_full: str | None = None   # "Corporation", "Labor", "Trade", etc.
    connected_organization_name: str | None = None  # the sponsoring company/org
    party: str | None = None
    state: str | None = None


class Disbursement(BaseModel):
    """Single itemized expenditure (Schedule B)."""
    model_config = {"extra": "ignore", "populate_by_name": True}

    recipient_name: str | None = None
    disbursement_purpose_category: str | None = None
    memo_text: str | None = None
    recipient_state: str | None = None
    amount: float = Field(0.0, alias="disbursement_amount")
    date: str | None = Field(None, alias="disbursement_date")


class CandidateSearchResult(BaseModel):
    model_config = {"extra": "ignore"}

    candidate_id: str
    name: str
    party: str | None = None
    office: str | None = None
    state: str | None = None
    election_years: list[int] = []


class IndependentExpenditure(BaseModel):
    """Aggregate super PAC / independent spending for or against a candidate (Schedule E)."""
    model_config = {"extra": "ignore"}

    committee_id: str | None = None
    committee_name: str | None = None
    candidate_id: str | None = None      # populated when querying by committee
    candidate_name: str | None = None
    support_oppose_indicator: str | None = None  # "S" (support) or "O" (oppose)
    total: float = 0.0
    count: int = 0


class EmployerContributions(BaseModel):
    """Total contributions grouped by donor employer (Schedule A by_employer)."""
    model_config = {"extra": "ignore"}

    employer: str | None = None
    total: float = 0.0
    count: int = 0


class ElectioneeringTotal(BaseModel):
    """Electioneering broadcast ad spend referencing a candidate, by committee."""
    model_config = {"extra": "ignore"}

    committee_id: str | None = None
    committee_name: str | None = None
    total: float = 0.0
    count: int = 0


class RecipientAggregate(BaseModel):
    """A committee's disbursements aggregated by recipient (schedule_b/by_recipient_id) —
    used to describe who a PAC funds."""
    model_config = {"extra": "ignore"}

    recipient_id: str | None = None
    recipient_name: str | None = None
    total: float | None = 0.0
    count: int | None = 0


class SizeBucket(BaseModel):
    """Receipts grouped by contribution size band (schedule_a/by_size) — used to describe
    how a PAC itself is funded (small-dollar vs large-dollar). The API may return null for
    size/count on some bands, so these are nullable."""
    model_config = {"extra": "ignore"}

    size: int | None = None   # lower bound of the band: 0, 200, 500, 1000, 2000
    total: float | None = 0.0
    count: int | None = 0
