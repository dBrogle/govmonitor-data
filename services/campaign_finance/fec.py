import itertools
import os
import threading
import time
import requests
from pathlib import Path

from .models import (
    CandidateTotals, CandidateSearchResult, Committee, CommitteeDetail, CommitteeTotals,
    Contribution, Disbursement, IndependentExpenditure, EmployerContributions, ElectioneeringTotal,
    RecipientAggregate, SizeBucket,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.cache import cache_path, load_cache, save_cache

BASE_URL = "https://api.open.fec.gov/v1"


class FECService:
    def __init__(self, api_key, retries: int = 8):
        # api_key may be a single key (str) or a pool (list). The rate limit is per key, so
        # we round-robin across the pool on every attempt — N keys ≈ N× throughput, and a
        # 429 on one key fails over to the next key on retry.
        self.api_keys = [api_key] if isinstance(api_key, str) else list(api_key)
        self._key_cycle = itertools.cycle(self.api_keys)
        self._key_lock = threading.Lock()
        self.retries = retries
        # A pooled session reuses connections and resolves each host's DNS once, instead
        # of a fresh lookup per request — important under parallel load, where a burst of
        # concurrent getaddrinfo calls can overwhelm the local resolver (NameResolutionError).
        self.session = requests.Session()

    def _next_key(self) -> str:
        with self._key_lock:
            return next(self._key_cycle)

    def _fetch_page(self, url: str, params: dict) -> dict:
        """Raw HTTP GET with retry on 429 and on read timeouts. Does not touch the cache."""
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params={**params, "api_key": self._next_key()}, timeout=30)
                if r.status_code == 429:
                    # 429s clear as the rolling rate window advances (seconds), so wait a
                    # short, capped interval and retry many times rather than escalating to
                    # long sleeps — minimizes wasted time and avoids dropping the call.
                    wait = min(2 ** attempt, 8)
                    print(f"  [rate limited, waiting {wait}s...]")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Transient read timeouts and DNS/connection blips are common under parallel
                # load; back off and retry rather than aborting (a single failure used to drop
                # an entire PAC profile — or, under a brief outage, every candidate at once).
                if attempt == self.retries - 1:
                    raise
                wait = 2 ** attempt * 2
                print(f"  [network error, retrying in {wait}s...] {type(e).__name__}")
                time.sleep(wait)
        raise RuntimeError(
            f"Failed after {self.retries} retries. "
            "Set OPEN_FEC_API_KEY to a real key from https://api.data.gov/signup/"
        )

    def _get_all_pages(self, endpoint: str, params: dict, folder: str) -> dict:
        """Fetch every page of results and cache the merged response.

        The cache key uses _pages=all so it doesn't collide with single-page
        fetches of the same endpoint. Delete the cache file to re-fetch.
        """
        cache_params = {k: v for k, v in params.items() if k != "api_key"}
        cache_params["_pages"] = "all"
        slug = endpoint.strip("/").replace("/", "_")
        path = cache_path(slug, cache_params, folder=folder)

        cached = load_cache(path)
        if cached is not None:
            print(f"  [cache] {folder}/{path.name}")
            return cached

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        all_results = []
        page = 1
        last_data = {}

        while True:
            page_params = {**params, "per_page": 100, "page": page}
            data = self._fetch_page(url, page_params)
            results = data.get("results", [])
            all_results.extend(results)
            last_data = data
            total_pages = data.get("pagination", {}).get("pages", 1)
            print(f"  [fetching page {page}/{total_pages}] {slug}")
            if page >= total_pages:
                break
            page += 1

        merged = {**last_data, "results": all_results}
        save_cache(path, merged)
        print(f"  [all {len(all_results)} results cached] {folder}/{path.name}")
        return merged

    def _get_pages(self, endpoint: str, params: dict, folder: str, max_pages: int) -> dict:
        """Fetch up to `max_pages` pages and cache the merged response.

        Like _get_all_pages but bounded — for endpoints (e.g. PAC receipts) where the
        full set can be hundreds of pages but only the top records, sorted, are needed.
        List-valued params (e.g. repeated line_number) are joined into a filename-safe
        cache key while still being sent to the API as repeated query parameters.
        """
        cache_params = {}
        for k, v in params.items():
            if k == "api_key":
                continue
            cache_params[k] = "+".join(map(str, v)) if isinstance(v, list) else v
        cache_params["_pages"] = f"max{max_pages}"
        slug = endpoint.strip("/").replace("/", "_")
        path = cache_path(slug, cache_params, folder=folder)

        cached = load_cache(path)
        if cached is not None:
            print(f"  [cache] {folder}/{path.name}")
            return cached

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        all_results = []
        page = 1
        last_data = {}

        while page <= max_pages:
            page_params = {**params, "per_page": 100, "page": page}
            data = self._fetch_page(url, page_params)
            all_results.extend(data.get("results", []))
            last_data = data
            total_pages = data.get("pagination", {}).get("pages", 1)
            print(f"  [fetching page {page}/{min(total_pages, max_pages)}] {slug}")
            if page >= total_pages:
                break
            page += 1

        merged = {**last_data, "results": all_results}
        save_cache(path, merged)
        print(f"  [{len(all_results)} results cached, capped at {max_pages} pages] {folder}/{path.name}")
        return merged

    def _get(self, endpoint: str, params: dict, folder: str) -> dict:
        """Fetch a single page from cache or API."""
        cache_params = {k: v for k, v in params.items() if k != "api_key"}
        slug = endpoint.strip("/").replace("/", "_")
        path = cache_path(slug, cache_params, folder=folder)

        cached = load_cache(path)
        if cached is not None:
            print(f"  [cache] {folder}/{path.name}")
            return cached

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        data = self._fetch_page(url, params)
        save_cache(path, data)
        print(f"  [fetched + cached] {folder}/{path.name}")
        return data

    def search_candidates(
        self,
        query: str,
        *,
        office: str | None = None,
        state: str | None = None,
        cycle: int | None = None,
    ) -> list[CandidateSearchResult]:
        params = {"q": query}
        if office:
            params["office"] = office
        if state:
            params["state"] = state
        if cycle:
            params["cycle"] = cycle
        data = self._get("candidates/search/", params, folder="fundraising/candidates")
        return [CandidateSearchResult.model_validate(r) for r in data.get("results", [])]

    def get_candidate_totals(self, candidate_id: str, cycle: int) -> CandidateTotals | None:
        data = self._get("candidates/totals/", {"candidate_id": candidate_id, "cycle": cycle}, folder="fundraising/totals")
        results = data.get("results", [])
        return CandidateTotals.model_validate(results[0]) if results else None

    def get_committees(self, candidate_id: str, cycle: int) -> list[Committee]:
        data = self._get(f"candidate/{candidate_id}/committees/", {"cycle": cycle}, folder="fundraising/committees")
        return [Committee.model_validate(r) for r in data.get("results", [])]

    def get_contributions(
        self, committee_id: str, cycle: int, *, individuals_only: bool | None = None, limit: int = 20
    ) -> list[Contribution]:
        # Fetch extra records because the FEC API ignores entity_type on schedule_a;
        # we filter client-side using the is_individual boolean instead.
        fetch_limit = limit if individuals_only is None else limit * 4
        params = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "sort": "-contribution_receipt_amount",
            "per_page": fetch_limit,
        }
        data = self._get("schedules/schedule_a/", params, folder="fundraising/contributions")
        contribs = [Contribution.model_validate(r) for r in data.get("results", [])]
        if individuals_only is True:
            contribs = [c for c in contribs if c.is_individual]
        elif individuals_only is False:
            contribs = [c for c in contribs if not c.is_individual]
        return contribs[:limit]

    def get_pac_contributions(self, committee_id: str, cycle: int, *, max_pages: int = 5) -> list[Contribution]:
        """PAC contributions (line 11C) received by a committee, server-side filtered.

        The FEC API filters Schedule A by form-line, so this returns only genuine PAC
        receipts — no need to over-fetch the top receipts and hope a PAC survives a
        client-side filter (the old approach, which silently returned nothing). Results
        are sorted by amount descending; the caller aggregates per contributor since a
        PAC's cycle total is usually split across multiple receipts (primary + general).
        F3-11C covers candidate committees (Form 3); F3X-11C covers PAC/JFC committees
        (Form 3X) we may also walk.
        """
        params = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "line_number": ["F3-11C", "F3X-11C"],
            "sort": "-contribution_receipt_amount",
        }
        data = self._get_pages(
            "schedules/schedule_a/", params, folder="fundraising/pac_contributions", max_pages=max_pages
        )
        return [Contribution.model_validate(r) for r in data.get("results", [])]

    def get_individual_contributions(self, committee_id: str, cycle: int, *, max_pages: int = 5) -> list[Contribution]:
        """Itemized individual contributions (line 11AI), server-side filtered + paginated,
        sorted by amount. Aggregating these by donor surfaces a candidate's real top
        backers — a single window of top receipts misses them, since donations are capped
        per election and the biggest backers give repeatedly across committees."""
        params = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "line_number": ["F3-11AI", "F3X-11AI"],
            "sort": "-contribution_receipt_amount",
        }
        data = self._get_pages(
            "schedules/schedule_a/", params, folder="fundraising/individual_contributions", max_pages=max_pages
        )
        return [Contribution.model_validate(r) for r in data.get("results", [])]

    def get_transfer_sources(self, committee_id: str, cycle: int, *, limit: int = 5) -> list[str]:
        """Committee ids that transferred funds INTO this committee (Schedule A line 12).

        These are the joint fundraising committees / leadership funds / authorized
        committees whose original donors are otherwise hidden behind a single 'transfer'
        line on the principal committee. Returns ids sorted by transfer size, so pulling
        their individual donors recovers the candidate's real large contributors."""
        params = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "line_number": ["F3-12", "F3X-12"],  # transfers from authorized committees
            "sort": "-contribution_receipt_amount",
            "per_page": 30,
        }
        data = self._get("schedules/schedule_a/", params, folder="fundraising/transfers_in")
        sources: list[str] = []
        seen = set()
        for r in data.get("results", []):
            cid = r.get("contributor_id")
            if cid and cid != committee_id and r.get("entity_type") in ("COM", "PTY", "CCM") and cid not in seen:
                seen.add(cid)
                sources.append(cid)
                if len(sources) >= limit:
                    break
        return sources

    def get_disbursements(self, committee_id: str, cycle: int, *, limit: int = 20) -> list[Disbursement]:
        data = self._get("schedules/schedule_b/", {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "sort": "-disbursement_amount",
            "per_page": limit,
        }, folder="fundraising/disbursements")
        return [Disbursement.model_validate(r) for r in data.get("results", [])]

    def get_committee_totals(self, committee_id: str, cycle: int) -> CommitteeTotals | None:
        """Full receipt breakdown for a committee, including small-dollar unitemized
        contributions (the component that the candidate totals endpoint omits)."""
        data = self._get(f"committee/{committee_id}/totals/", {"cycle": cycle}, folder="fundraising/committee_totals")
        results = data.get("results", [])
        return CommitteeTotals.model_validate(results[0]) if results else None

    def get_committee_detail(self, committee_id: str) -> CommitteeDetail | None:
        data = self._get(f"committee/{committee_id}/", {}, folder="fundraising/committee_details")
        results = data.get("results", [])
        return CommitteeDetail.model_validate(results[0]) if results else None

    def get_independent_expenditures(self, candidate_id: str, cycle: int) -> list[IndependentExpenditure]:
        """Super PAC and independent group spending for/against a candidate (Schedule E aggregates).
        Exhausts all pages so the full picture is cached."""
        data = self._get_all_pages("schedules/schedule_e/by_candidate/", {
            "candidate_id": candidate_id,
            "cycle": cycle,
        }, folder="fundraising/independent_expenditures")
        results = [IndependentExpenditure.model_validate(r) for r in data.get("results", [])]
        results.sort(key=lambda r: r.total, reverse=True)
        return results

    def get_committee_independent_expenditures(self, committee_id: str, cycle: int) -> list[IndependentExpenditure]:
        """A committee's independent expenditures aggregated by candidate (Schedule E) — i.e.
        which candidates this PAC spent to support/oppose. Empty for PACs that make none."""
        # election_full=false is required when filtering by committee (the default full-election
        # view demands a candidate_id/office); it also scopes totals to this cycle.
        data = self._get_all_pages("schedules/schedule_e/by_candidate/", {
            "committee_id": committee_id,
            "cycle": cycle,
            "election_full": "false",
        }, folder="fundraising/ie_by_committee")
        results = [IndependentExpenditure.model_validate(r) for r in data.get("results", [])]
        results.sort(key=lambda r: r.total, reverse=True)
        return results

    def get_contributions_by_employer(self, committee_id: str, cycle: int, *, limit: int = 20) -> list[EmployerContributions]:
        """Top employer sources of individual contributions to a committee (Schedule A aggregates)."""
        data = self._get("schedules/schedule_a/by_employer/", {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "per_page": limit,
            "sort": "-total",
        }, folder="fundraising/contributions_by_employer")
        return [EmployerContributions.model_validate(r) for r in data.get("results", [])]

    def get_disbursements_by_recipient(self, committee_id: str, cycle: int, *, limit: int = 10) -> list[RecipientAggregate]:
        """Top recipients of a committee's disbursements (schedule_b/by_recipient_id).

        Describes who a PAC funds. recipient_id lets the caller resolve each recipient's
        party (via get_committee_detail) to compute a partisan lean."""
        data = self._get("schedules/schedule_b/by_recipient_id/", {
            "committee_id": committee_id,
            "cycle": cycle,
            "sort": "-total",
            "per_page": limit,
        }, folder="fundraising/pac_recipients")
        return [RecipientAggregate.model_validate(r) for r in data.get("results", [])]

    def get_receipts_by_size(self, committee_id: str, cycle: int) -> list[SizeBucket]:
        """A committee's receipts grouped by contribution size band (schedule_a/by_size).
        Describes how a PAC itself is funded — small-dollar vs large-dollar."""
        data = self._get("schedules/schedule_a/by_size/", {
            "committee_id": committee_id,
            "cycle": cycle,
        }, folder="fundraising/pac_receipts_by_size")
        return [SizeBucket.model_validate(r) for r in data.get("results", [])]

    def get_electioneering(self, candidate_id: str, cycle: int) -> list[ElectioneeringTotal]:
        """Broadcast/cable ad spend referencing a candidate within 60/30 days of an election."""
        data = self._get("electioneering/by_candidate/", {
            "candidate_id": candidate_id,
            "cycle": cycle,
        }, folder="fundraising/electioneering")
        return [ElectioneeringTotal.model_validate(r) for r in data.get("results", [])]
