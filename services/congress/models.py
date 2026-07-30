from pydantic import BaseModel, Field, ConfigDict, confloat


class MemberSummary(BaseModel):
    """Member item returned from a district/congress list endpoint."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bioguide_id: str = Field(alias="bioguideId")
    name: str
    party_name: str | None = Field(None, alias="partyName")
    state: str | None = None
    district: int | None = None


class MemberDetail(BaseModel):
    """Full member detail from /member/{bioguideId}."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bioguide_id: str = Field(alias="bioguideId")
    name: str = Field(alias="directOrderName")
    state: str | None = None
    district: int | None = None
    birth_year: str | None = Field(None, alias="birthYear")
    official_website_url: str | None = Field(None, alias="officialWebsiteUrl")
    party_history: list[dict] | None = Field(None, alias="partyHistory")
    terms: list[dict] | None = None

    @property
    def current_party(self) -> str | None:
        if not self.party_history:
            return None
        return self.party_history[-1].get("partyName")

    @property
    def serving_since(self) -> int | None:
        if not self.party_history:
            return None
        return self.party_history[0].get("startYear")


class BillSummary(BaseModel):
    """Bill item from a sponsored/cosponsored legislation list."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    congress: int
    number: str
    type: str
    title: str | None = None
    introduced_date: str | None = Field(None, alias="introducedDate")
    latest_action: dict | None = Field(None, alias="latestAction")

    @property
    def latest_action_date(self) -> str | None:
        return (self.latest_action or {}).get("actionDate")

    @property
    def latest_action_text(self) -> str | None:
        return (self.latest_action or {}).get("text")


class BillAction(BaseModel):
    """A single action on a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    action_date: str | None = Field(None, alias="actionDate")
    text: str | None = None
    type: str | None = None


class BillSummaryText(BaseModel):
    """A summary text entry for a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    action_date: str | None = Field(None, alias="actionDate")
    action_desc: str | None = Field(None, alias="actionDesc")
    text: str | None = None
    update_date: str | None = Field(None, alias="updateDate")


class BillDetail(BaseModel):
    """Full bill detail assembled from the bill, actions, and summaries endpoints."""
    congress: int
    number: str
    type: str
    title: str | None = None
    introduced_date: str | None = None
    origin_chamber: str | None = None
    policy_area: str | None = None
    sponsors: list[dict] | None = None
    latest_action_date: str | None = None
    latest_action_text: str | None = None
    actions: list[BillAction] = []
    summaries: list[BillSummaryText] = []


class BillAmendment(BaseModel):
    """An amendment to a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    number: str | None = None
    type: str | None = None
    congress: int | None = None
    description: str | None = None
    latest_action: dict | None = Field(None, alias="latestAction")
    purpose: str | None = None
    url: str | None = None


class BillCommittee(BaseModel):
    """A committee associated with a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str | None = None
    chamber: str | None = None
    type: str | None = None
    system_code: str | None = Field(None, alias="systemCode")
    url: str | None = None
    activities: list[dict] | None = None


class BillCosponsor(BaseModel):
    """A cosponsor of a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bioguide_id: str = Field(alias="bioguideId")
    full_name: str | None = Field(None, alias="fullName")
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    party: str | None = None
    state: str | None = None
    district: int | None = None
    sponsorship_date: str | None = Field(None, alias="sponsorshipDate")
    is_original_cosponsor: bool | None = Field(None, alias="isOriginalCosponsor")


class BillRelatedBill(BaseModel):
    """A bill related to another bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    congress: int | None = None
    number: int | None = None
    type: str | None = None
    title: str | None = None
    relationship_details: list[dict] | None = Field(None, alias="relationshipDetails")
    latest_action: dict | None = Field(None, alias="latestAction")


class BillSubject(BaseModel):
    """A legislative subject associated with a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str | None = None
    update_date: str | None = Field(None, alias="updateDate")


class BillTextVersion(BaseModel):
    """A text version of a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    date: str | None = None
    type: str | None = None
    url: str | None = None
    formats: list[dict] | None = None


class BillTitle(BaseModel):
    """A title for a bill."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    title: str | None = None
    title_type: str | None = Field(None, alias="titleType")
    title_type_code: int | None = Field(None, alias="titleTypeCode")
    chamber: str | None = None
    bill_text_version_name: str | None = Field(None, alias="billTextVersionName")
    bill_text_version_code: str | None = Field(None, alias="billTextVersionCode")


class LawSummary(BaseModel):
    """Law item from a law list endpoint."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    congress: int | None = None
    number: str | None = None
    type: str | None = None
    title: str | None = None
    url: str | None = None
    latest_action: dict | None = Field(None, alias="latestAction")

    @property
    def latest_action_date(self) -> str | None:
        return (self.latest_action or {}).get("actionDate")

    @property
    def latest_action_text(self) -> str | None:
        return (self.latest_action or {}).get("text")


class HouseVoteRecord(BaseModel):
    """A single House roll call vote from the houseRollCallVotes list endpoint.

    Actual API field names differ from the docs — verified against cached responses.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    congress: int
    session: int = Field(alias="sessionNumber")
    vote_number: int = Field(alias="rollCallNumber")
    # startDate is an ISO datetime; we keep it as-is and slice to date in the property
    vote_date_raw: str | None = Field(None, alias="startDate")
    vote_question: str | None = Field(None, alias="voteType")
    vote_result: str | None = Field(None, alias="result")
    legislation_number: str | None = Field(None, alias="legislationNumber")
    legislation_type: str | None = Field(None, alias="legislationType")

    @property
    def vote_date(self) -> str | None:
        if not self.vote_date_raw:
            return None
        return self.vote_date_raw[:10]  # "2025-09-08T18:56:00-04:00" → "2025-09-08"

    @property
    def bill(self) -> dict | None:
        if self.legislation_number and self.legislation_type:
            return {"number": self.legislation_number, "type": self.legislation_type}
        return None


class VoteMember(BaseModel):
    """How a single member voted on a specific roll call vote."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    bioguide_id: str = Field(alias="bioguideID")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    party: str | None = Field(None, alias="voteParty")
    state: str | None = Field(None, alias="voteState")
    vote_position: str = Field(alias="voteCast")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class MemberVoteRecord(BaseModel):
    """A member's position on a specific house vote (assembled client-side)."""
    # Vote numbers restart each session, so a roll call is only identified by the pair.
    session: int | None = None
    vote_number: int
    vote_date: str | None
    vote_question: str | None
    vote_result: str | None
    member_position: str
    bill: dict | None


# ── Bill Analysis (LLM-scored) ────────────────────────────────────────────


class TopicScoreItem(BaseModel):
    """One topic's evaluation within a multi-topic LLM response.

    Field order matters: thoughts come first so the model reasons before scoring.
    """
    topic_slug: str
    thoughts: str
    score: confloat(ge=-1.0, le=1.0)


class BillAnalysisResponse(BaseModel):
    """LLM response model for evaluating a bill against all topics at once.

    Used as the JSON schema for structured output from the LLM.
    """
    topics: list[TopicScoreItem]


class BillTopicScore(BaseModel):
    """A persisted score for one bill on one topic, including metadata."""
    thoughts: str
    topic_slug: str
    topic_name: str
    score: float


class BillSummaryResponse(BaseModel):
    """LLM response model for a concise bill summary."""
    summary: str


class BillAnalysis(BaseModel):
    """Full analysis of a bill across all evaluated topics."""
    congress: int
    bill_type: str
    bill_number: str
    summary: str | None = None
    # Which text the LLM scored: "summary" (CRS), "full_text", or "full_text_truncated".
    # Provenance so we know whether a score rests on the official summary or the full bill.
    text_source: str | None = None
    scores: list[BillTopicScore] = []
