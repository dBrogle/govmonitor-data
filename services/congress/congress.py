import hashlib
import itertools
import json
import os
import re
import threading
import time
import requests
from pathlib import Path

from .models import (
    MemberSummary, MemberDetail,
    BillSummary, BillDetail, BillAction, BillSummaryText,
    BillAmendment, BillCommittee, BillCosponsor,
    BillRelatedBill, BillSubject, BillTextVersion, BillTitle,
    LawSummary,
    HouseVoteRecord, VoteMember, MemberVoteRecord,
    BillAnalysisResponse, BillTopicScore, BillAnalysis,
    BillSummaryResponse,
)
from .topics import TopicConfig, TOPICS, TOPICS_BY_SLUG
from .prompts import (
    BILL_ANALYSIS_SYSTEM_PROMPT, build_topics_user_prompt,
    BILL_SUMMARY_SYSTEM_PROMPT, build_summary_user_prompt,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.cache import cache_path, load_cache, save_cache, CACHE_DIR

BASE_URL = "https://api.congress.gov/v3"


class CongressService:
    def __init__(self, api_key, retries: int = 5, llm_service=None):
        # api_key may be a single key (str) or a pool (list). Congress.gov and OpenFEC both
        # run on api.data.gov, so the same keys serve both; the rate limit is per key, so we
        # round-robin across the pool on every attempt (N keys ≈ N× throughput, 429 fails over).
        self.api_keys = [api_key] if isinstance(api_key, str) else list(api_key)
        self._key_cycle = itertools.cycle(self.api_keys)
        self._key_lock = threading.Lock()
        self.retries = retries
        self.llm_service = llm_service  # Optional: services.llm.base.LLMService
        # Pooled session: reuse connections and resolve each host's DNS once rather than
        # per request — under parallel load a burst of concurrent lookups can overwhelm
        # the local resolver (NameResolutionError).
        self.session = requests.Session()

    def _next_key(self) -> str:
        with self._key_lock:
            return next(self._key_cycle)

    # ── Internal HTTP / cache helpers ──────────────────────────────────────

    def _fetch_page(self, url: str, params: dict) -> dict:
        """Raw HTTP GET with retry on 429 and on transient timeouts/connection errors.
        Never touches the cache."""
        for attempt in range(self.retries):
            try:
                r = self.session.get(
                    url,
                    params={**params, "api_key": self._next_key(), "format": "json"},
                    timeout=30,
                )
                if r.status_code == 429:
                    wait = 2 ** attempt * 5
                    print(f"  [rate limited, waiting {wait}s...]")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Transient network blips shouldn't abort a whole stage; back off and retry.
                if attempt == self.retries - 1:
                    raise
                wait = 2 ** attempt * 2
                print(f"  [network error, retrying in {wait}s...] {type(e).__name__}")
                time.sleep(wait)
        raise RuntimeError(
            f"Failed after {self.retries} retries. "
            "Ensure CONGRESS_API_KEY is set in .env — get a key at https://api.data.gov/signup/"
        )

    def _fetch_raw(self, url: str, cache_file: Path) -> str:
        """Download a raw file (XML, HTML, etc.) with retry and file-based cache.

        Unlike _fetch_page, this fetches from arbitrary URLs (e.g. congress.gov
        bill text) and caches the raw text to disk.
        """
        if cache_file.exists():
            print(f"  [cache] {cache_file.relative_to(CACHE_DIR)}")
            return cache_file.read_text(encoding="utf-8")

        for attempt in range(self.retries):
            try:
                r = self.session.get(url, timeout=30)
                if r.status_code == 429:
                    wait = 2 ** attempt * 5
                    print(f"  [rate limited, waiting {wait}s...]")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(r.text, encoding="utf-8")
                print(f"  [fetched] {cache_file.relative_to(CACHE_DIR)}")
                return r.text
            except requests.exceptions.Timeout:
                raise
        raise RuntimeError(f"Rate limited after {self.retries} retries fetching {url}")

    def _get(self, endpoint: str, params: dict, subfolder: str) -> dict:
        """Fetch a single page, using a per-subfolder cache file."""
        slug = endpoint.strip("/").replace("/", "_")
        path = cache_path(slug, params, folder=subfolder)

        cached = load_cache(path)
        if cached is not None:
            print(f"  [cache] {subfolder}/{path.name}")
            return cached

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        data = self._fetch_page(url, params)
        save_cache(path, data)
        print(f"  [fetched] {subfolder}/{path.name}")
        return data

    def _get_all_pages(
        self, endpoint: str, params: dict, subfolder: str, results_key: str,
        max_pages: int | None = None,
    ) -> dict:
        """Exhaust all pages (or up to max_pages) and cache the merged response.

        Uses _pages in the cache key to avoid colliding with single-page fetches of the
        same endpoint. max_pages bounds endpoints (e.g. sponsored/cosponsored legislation)
        that return thousands of items newest-first when only the most recent are kept.
        """
        cache_params = {**params, "_pages": "all" if max_pages is None else f"max{max_pages}"}
        slug = endpoint.strip("/").replace("/", "_")
        path = cache_path(slug, cache_params, folder=subfolder)

        cached = load_cache(path)
        if cached is not None:
            print(f"  [cache] {subfolder}/{path.name}")
            return cached

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        all_results: list = []
        offset = 0
        per_page = 250
        pages = 0
        last_data: dict = {}

        while True:
            page_params = {**params, "limit": per_page, "offset": offset}
            data = self._fetch_page(url, page_params)
            items = data.get(results_key, [])
            all_results.extend(items)
            last_data = data
            pages += 1
            total = data.get("pagination", {}).get("count", 0)
            fetched = offset + len(items)
            print(f"  [fetching {fetched}/{total}] {slug}")
            if fetched >= total or not items or (max_pages is not None and pages >= max_pages):
                break
            offset += per_page

        merged = {**last_data, results_key: all_results}
        save_cache(path, merged)
        print(f"  [{len(all_results)} results cached] {path.name}")
        return merged

    # ── Members ────────────────────────────────────────────────────────────

    def get_member(self, bioguide_id: str) -> MemberDetail | None:
        """Full detail for a member by bioguide ID."""
        data = self._get(f"member/{bioguide_id}", {}, subfolder="members")
        member_data = data.get("member")
        if not member_data:
            return None
        return MemberDetail.model_validate(member_data)

    def get_members_by_district(
        self, congress: int, state: str, district: int
    ) -> list[MemberSummary]:
        """Members representing a given state/district in a specific congress."""
        data = self._get(
            f"member/congress/{congress}/{state}/{district}",
            {},
            subfolder="members",
        )
        return [MemberSummary.model_validate(m) for m in data.get("members", [])]

    # ── Legislation ────────────────────────────────────────────────────────

    def get_sponsored_legislation(
        self, bioguide_id: str, congress: int | None = None
    ) -> list[BillSummary]:
        """All bills sponsored by a member, optionally filtered to a congress."""
        # Items come newest-first, so 2 pages (500) covers the current congress; we only
        # keep the most recent ~50, so exhausting thousands of older items is wasteful.
        data = self._get_all_pages(
            f"member/{bioguide_id}/sponsored-legislation",
            {},
            subfolder="sponsored",
            results_key="sponsoredLegislation",
            max_pages=2,
        )
        items = data.get("sponsoredLegislation", [])
        if congress is not None:
            items = [b for b in items if b.get("congress") == congress]
        # Sponsored legislation can include amendments (amendmentNumber instead of
        # number); skip those since BillSummary only models bills.
        bills = [b for b in items if b.get("number") is not None and b.get("type") is not None]
        return [BillSummary.model_validate(b) for b in bills]

    def get_cosponsored_legislation(
        self, bioguide_id: str, congress: int | None = None
    ) -> list[BillSummary]:
        """All bills cosponsored by a member, optionally filtered to a congress."""
        # See get_sponsored_legislation: cap to the newest 2 pages (we keep ~50).
        data = self._get_all_pages(
            f"member/{bioguide_id}/cosponsored-legislation",
            {},
            subfolder="cosponsored",
            results_key="cosponsoredLegislation",
            max_pages=2,
        )
        items = data.get("cosponsoredLegislation", [])
        if congress is not None:
            items = [b for b in items if b.get("congress") == congress]
        bills = [b for b in items if b.get("number") is not None and b.get("type") is not None]
        return [BillSummary.model_validate(b) for b in bills]

    def get_bills(
        self, congress: int | None = None, bill_type: str | None = None, limit: int = 20
    ) -> list[BillSummary]:
        """List bills, optionally filtered by congress and/or bill type.

        Maps to:
          GET /bill
          GET /bill/{congress}
          GET /bill/{congress}/{billType}
        """
        if congress and bill_type:
            endpoint = f"bill/{congress}/{bill_type.lower()}"
        elif congress:
            endpoint = f"bill/{congress}"
        else:
            endpoint = "bill"
        data = self._get(endpoint, {"limit": limit}, subfolder="bills/list")
        return [BillSummary.model_validate(b) for b in data.get("bills", [])]

    def get_bill_detail(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> BillDetail | None:
        """Full bill detail including actions and summaries.

        Makes three API calls (bill, actions, summaries), each cached
        independently under their own bills/* subfolders.
        """
        bt = bill_type.lower()
        bn = str(bill_number)
        base = f"bill/{congress}/{bt}/{bn}"

        bill_data = self._get(base, {}, subfolder="bills/detail")
        bill = bill_data.get("bill")
        if not bill:
            return None

        actions_data = self._get(f"{base}/actions", {}, subfolder="bills/actions")
        actions = [BillAction.model_validate(a) for a in actions_data.get("actions", [])]

        summaries_data = self._get(f"{base}/summaries", {}, subfolder="bills/summaries")
        summaries = [
            BillSummaryText.model_validate(s) for s in summaries_data.get("summaries", [])
        ]

        latest_action = bill.get("latestAction") or {}
        policy_area_obj = bill.get("policyArea") or {}

        return BillDetail(
            congress=bill.get("congress"),
            number=bill.get("number"),
            type=bill.get("type"),
            title=bill.get("title"),
            introduced_date=bill.get("introducedDate"),
            origin_chamber=bill.get("originChamber"),
            policy_area=policy_area_obj.get("name"),
            sponsors=bill.get("sponsors"),
            latest_action_date=latest_action.get("actionDate"),
            latest_action_text=latest_action.get("text"),
            actions=actions,
            summaries=summaries,
        )

    def get_bill_actions(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillAction]:
        """Actions on a specified bill. Cached under bills/actions/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/actions", {}, subfolder="bills/actions")
        return [BillAction.model_validate(a) for a in data.get("actions", [])]

    def get_bill_amendments(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillAmendment]:
        """Amendments to a specified bill. Cached under bills/amendments/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/amendments", {}, subfolder="bills/amendments")
        return [BillAmendment.model_validate(a) for a in data.get("amendments", [])]

    def get_bill_committees(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillCommittee]:
        """Committees associated with a specified bill. Cached under bills/committees/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/committees", {}, subfolder="bills/committees")
        return [BillCommittee.model_validate(c) for c in data.get("committees", [])]

    def get_bill_cosponsors(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillCosponsor]:
        """Cosponsors on a specified bill. Cached under bills/cosponsors/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/cosponsors", {}, subfolder="bills/cosponsors")
        return [BillCosponsor.model_validate(c) for c in data.get("cosponsors", [])]

    def get_bill_related_bills(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillRelatedBill]:
        """Related bills to a specified bill. Cached under bills/relatedbills/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/relatedbills", {}, subfolder="bills/relatedbills")
        return [BillRelatedBill.model_validate(b) for b in data.get("relatedBills", [])]

    def get_bill_subjects(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillSubject]:
        """Legislative subjects on a specified bill. Cached under bills/subjects/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/subjects", {}, subfolder="bills/subjects")
        subjects = data.get("subjects", {})
        # API nests subjects under legislativeSubjects.items
        leg_subjects = subjects.get("legislativeSubjects", [])
        if isinstance(leg_subjects, dict):
            leg_subjects = leg_subjects.get("items", [])
        policy_area = subjects.get("policyArea")
        result = [BillSubject.model_validate(s) for s in leg_subjects]
        if policy_area:
            result.insert(0, BillSubject.model_validate(policy_area))
        return result

    def get_bill_summaries(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillSummaryText]:
        """Summaries for a specified bill. Cached under bills/summaries/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/summaries", {}, subfolder="bills/summaries")
        return [BillSummaryText.model_validate(s) for s in data.get("summaries", [])]

    def get_bill_text(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillTextVersion]:
        """Text versions for a specified bill. Cached under bills/text/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/text", {}, subfolder="bills/text")
        return [BillTextVersion.model_validate(t) for t in data.get("textVersions", [])]

    def get_bill_text_xml(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> str | None:
        """Fetch the XML of the most recent text version of a bill.

        Looks up text versions via the API, finds the latest one with an
        XML format URL, downloads it, and caches to bills/text_xml/.
        Returns the raw XML string, or None if no XML is available.
        """
        versions = self.get_bill_text(congress, bill_type, bill_number)
        if not versions:
            return None

        # Versions are ordered most-recent first from the API
        for version in versions:
            xml_url = None
            for fmt in (version.formats or []):
                if fmt.get("type") == "Formatted XML":
                    xml_url = fmt.get("url")
                    break
            if xml_url:
                bt = bill_type.lower()
                bn = str(bill_number)
                # Extract the filename from the URL for a readable cache name
                url_filename = xml_url.rsplit("/", 1)[-1]  # e.g. BILLS-119hr28eh.xml
                cache_file = CACHE_DIR / "bills" / "text_xml" / f"{congress}_{bt}_{bn}_{url_filename}"
                return self._fetch_raw(xml_url, cache_file)

        return None

    def get_bill_titles(
        self, congress: int, bill_type: str, bill_number: str | int
    ) -> list[BillTitle]:
        """Titles for a specified bill. Cached under bills/titles/."""
        base = f"bill/{congress}/{bill_type.lower()}/{bill_number}"
        data = self._get(f"{base}/titles", {}, subfolder="bills/titles")
        return [BillTitle.model_validate(t) for t in data.get("titles", [])]

    # ── Laws ───────────────────────────────────────────────────────────────

    def get_laws(
        self, congress: int, law_type: str | None = None, limit: int = 20
    ) -> list[LawSummary]:
        """List laws filtered by congress and optionally law type (pub or priv).

        Maps to:
          GET /law/{congress}
          GET /law/{congress}/{lawType}
        """
        if law_type:
            endpoint = f"law/{congress}/{law_type.lower()}"
        else:
            endpoint = f"law/{congress}"
        data = self._get(endpoint, {"limit": limit}, subfolder="laws/list")
        return [LawSummary.model_validate(l) for l in data.get("bills", [])]

    def get_law(
        self, congress: int, law_type: str, law_number: str | int
    ) -> LawSummary | None:
        """Detail for a specific law. Cached under laws/detail/."""
        endpoint = f"law/{congress}/{law_type.lower()}/{law_number}"
        data = self._get(endpoint, {}, subfolder="laws/detail")
        bill = data.get("bill")
        if not bill:
            return None
        return LawSummary.model_validate(bill)

    # ── Votes ──────────────────────────────────────────────────────────────

    def get_house_votes(self, congress: int, session: int) -> list[HouseVoteRecord]:
        """Fetch the first page (up to 250) of House roll call votes for a
        congress/session, sorted most-recent first by default."""
        data = self._get(
            f"house-vote/{congress}/{session}",
            {"limit": 250},
            subfolder="house_votes",
        )
        # API returns results under houseRollCallVotes, not houseVotes
        return [HouseVoteRecord.model_validate(v) for v in data.get("houseRollCallVotes", [])]

    def get_vote_members(
        self, congress: int, session: int, vote_number: int
    ) -> list[VoteMember]:
        """How each member voted on a specific roll call vote."""
        data = self._get(
            f"house-vote/{congress}/{session}/{vote_number}/members",
            {},
            subfolder="vote_members",
        )
        # Response structure: {"houseRollCallVoteMemberVotes": {"results": [...]}}
        vote_data = data.get("houseRollCallVoteMemberVotes", {})
        members_raw = vote_data.get("results", [])
        result = []
        for m in members_raw:
            try:
                result.append(VoteMember.model_validate(m))
            except Exception:
                pass
        return result

    def get_member_voting_history(
        self,
        bioguide_id: str,
        congress: int,
        session: int,
        limit: int = 20,
    ) -> list[MemberVoteRecord]:
        """Return the member's position on the most recent `limit` roll call votes.

        Fetches the vote list once (cached), then fetches per-vote member lists
        (each cached individually under vote_members/).
        """
        votes = self.get_house_votes(congress, session)[:limit]
        result = []
        for vote in votes:
            try:
                members = self.get_vote_members(congress, session, vote.vote_number)
            except Exception as e:
                print(f"  [warning] vote {vote.vote_number}: {e}")
                continue
            member_vote = next(
                (m for m in members if m.bioguide_id == bioguide_id), None
            )
            if member_vote:
                result.append(
                    MemberVoteRecord(
                        vote_number=vote.vote_number,
                        vote_date=vote.vote_date,
                        vote_question=vote.vote_question,
                        vote_result=vote.vote_result,
                        member_position=member_vote.vote_position,
                        bill=vote.bill,
                    )
                )
        return result

    # ── Bill Analysis (LLM) ───────────────────────────────────────────────

    @staticmethod
    def _topics_hash(topics: list[TopicConfig]) -> str:
        """Deterministic short hash of the full topic list configuration.

        Any change to topic slugs, names, or descriptions invalidates the cache.
        """
        content = "|".join(
            f"{t.slug}:{t.name}:{t.minus_one_desc}:{t.plus_one_desc}"
            for t in sorted(topics, key=lambda t: t.slug)
        )
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _analysis_cache_path(
        self, congress: int, bill_type: str, bill_number: str, topics: list[TopicConfig]
    ) -> Path:
        """Cache path for a bill's full topic analysis.

        Keyed on both the topic list AND the scoring system prompt, so that any change to
        the topics OR the prompt invalidates stale analyses (a prompt change alters the
        scores, so reusing a cache from the old prompt would silently serve wrong data)."""
        bill_slug = f"{congress}_{bill_type.lower()}_{bill_number}"
        prompt_hash = hashlib.sha256(BILL_ANALYSIS_SYSTEM_PROMPT.encode()).hexdigest()[:6]
        key = f"{self._topics_hash(topics)}_{prompt_hash}"
        return CACHE_DIR / "bills" / "analysis" / bill_slug / f"{key}.json"

    def _summary_cache_path(
        self, congress: int, bill_type: str, bill_number: str
    ) -> Path:
        """Cache path for a bill's LLM-generated summary."""
        bill_slug = f"{congress}_{bill_type.lower()}_{bill_number}"
        return CACHE_DIR / "bills" / "summary" / f"{bill_slug}.json"

    def get_bill_crs_summary(
        self, congress: int, bill_type: str, bill_number: str
    ) -> str | None:
        """The latest official (CRS) summary for a bill as plain text, or None.

        CRS summaries are neutral, compact descriptions of a bill's main thrust. ~Half of
        bills have one (CRS lags introduction; minor bills often never get one)."""
        try:
            summaries = self.get_bill_summaries(congress, bill_type, bill_number)
        except Exception:
            return None
        # Last entry is the most recent summary version.
        texts = [s.text for s in summaries if s.text and s.text.strip()]
        if not texts:
            return None
        plain = re.sub(r"<[^>]+>", " ", texts[-1])      # strip HTML tags
        plain = re.sub(r"\s+", " ", plain).strip()       # collapse whitespace
        return plain or None

    # Max characters of full bill text to send before falling back to the summary. ~150K
    # tokens — safely inside a 1M-context model while bounding cost/latency on omnibus bills
    # (the NDAA's full text alone is ~1.26M tokens and would overflow context entirely).
    MAX_ANALYSIS_CHARS = 600_000

    def _select_analysis_input(
        self, congress: int, bill_type: str, bill_number: str
    ) -> tuple[str, str]:
        """Choose the text to score and report its provenance.

        FULL-TEXT-FIRST. The full bill text is the most accurate input: a generic CRS summary
        can strip a bill's political valence entirely — e.g. a budget resolution that enables
        tax cuts reads in summary as neutral "recommended levels for federal revenues," which
        the model mis-scored as a tax *increase* (a sign flip that swung the whole House's
        taxation alignment in testing). So prefer full text, and fall back to the CRS summary
        ONLY when the full text would overflow the model context (omnibus bills like the
        NDAA), where the centrality prompt + policy-area targeting already guard against a
        buried provision dominating. Truncated full text is the last resort.

        Returns (text, source), source ∈ {full_text, summary, full_text_truncated}.
        Raises ValueError if no text is available at all."""
        bill_xml = self.get_bill_text_xml(congress, bill_type, bill_number)
        if bill_xml is not None and len(bill_xml) <= self.MAX_ANALYSIS_CHARS:
            return bill_xml, "full_text"

        # Full text is oversized or missing — the summary is the better choice here (it keeps a
        # huge bill within context, and centrality/targeting handle the lost detail).
        summary = self.get_bill_crs_summary(congress, bill_type, bill_number)
        if summary:
            return summary, "summary"
        if bill_xml is not None:
            return bill_xml[: self.MAX_ANALYSIS_CHARS], "full_text_truncated"
        raise ValueError(
            f"No summary or XML text available for bill {congress}/{bill_type}/{bill_number}. "
            "Fetch the bill text first."
        )

    def analyze_bill(
        self,
        congress: int,
        bill_type: str,
        bill_number: str | int,
        topics: list[TopicConfig] | None = None,
        force: bool = False,
    ) -> BillAnalysis:
        """Analyze a bill's alignment with political topics using the LLM.

        Fetches the bill XML (cached), then sends a single LLM call to score
        it against all topics at once. The result is cached per bill, keyed on a hash of
        the topic list AND the scoring prompt, so any change to either invalidates it.

        Args:
            congress: Congress number (e.g. 119).
            bill_type: Bill type slug (e.g. "hr", "hjres").
            bill_number: Bill number.
            topics: Optional list of TopicConfig to evaluate. Defaults to all.
            force: Bypass the cache read (always re-score). Used by tests and re-runs.

        Returns:
            BillAnalysis with a score for each topic.
        """
        bt = bill_type.lower()
        bn = str(bill_number)
        topics = topics or TOPICS
        topics_by_slug = {t.slug: t for t in topics}

        # Check cache
        cache_file = self._analysis_cache_path(congress, bt, bn, topics)
        if not force:
            cached = load_cache(cache_file)
            if cached is not None:
                print(f"  [cache] analysis/{cache_file.parent.name}/{cache_file.name}")
                return BillAnalysis.model_validate(cached)

        # Need LLM
        if self.llm_service is None:
            raise RuntimeError(
                "LLM service required for bill analysis. "
                "Pass llm_service= to CongressService."
            )

        bill_text, text_source = self._select_analysis_input(congress, bt, bn)

        user_prompt = build_topics_user_prompt(bill_text, topics)

        response: BillAnalysisResponse = self.llm_service.structured_completion(
            system_prompt=BILL_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=BillAnalysisResponse,
        )

        scores: list[BillTopicScore] = []
        for item in response.topics:
            topic_cfg = topics_by_slug.get(item.topic_slug)
            topic_name = topic_cfg.name if topic_cfg else item.topic_slug
            scores.append(BillTopicScore(
                topic_slug=item.topic_slug,
                topic_name=topic_name,
                score=item.score,
                thoughts=item.thoughts,
            ))

        analysis = BillAnalysis(
            congress=congress,
            bill_type=bt,
            bill_number=bn,
            text_source=text_source,
            scores=scores,
        )

        save_cache(cache_file, analysis.model_dump())
        print(f"  [analyzed] {len(scores)} topics scored")
        return analysis

    def summarize_bill(
        self,
        congress: int,
        bill_type: str,
        bill_number: str | int,
    ) -> str:
        """Generate a concise 1-2 sentence LLM summary of a bill.

        Fetches the bill XML (cached), then sends a single LLM call to
        produce a plain-language summary. The result is cached per bill.

        Returns:
            The summary string.
        """
        bt = bill_type.lower()
        bn = str(bill_number)

        # Check cache
        cache_file = self._summary_cache_path(congress, bt, bn)
        cached = load_cache(cache_file)
        if cached is not None:
            print(f"  [cache] summary/{cache_file.name}")
            return cached["summary"]

        # Need LLM
        if self.llm_service is None:
            raise RuntimeError(
                "LLM service required for bill summarization. "
                "Pass llm_service= to CongressService."
            )

        bill_xml = self.get_bill_text_xml(congress, bt, bn)
        if bill_xml is None:
            raise ValueError(
                f"No XML text available for bill {congress}/{bt}/{bn}."
            )

        user_prompt = build_summary_user_prompt(bill_xml)

        response: BillSummaryResponse = self.llm_service.structured_completion(
            system_prompt=BILL_SUMMARY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=BillSummaryResponse,
        )

        save_cache(cache_file, {"summary": response.summary})
        print(f"  [summarized] {congress}/{bt}/{bn}")
        return response.summary
