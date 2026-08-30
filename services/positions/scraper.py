"""Fetch a House member's official site and gather their public-statement text.

Official house.gov sites keep members' *stated* positions in press releases — the "Issues"
pages are usually thin, boilerplate, or JavaScript-gated. So we do a shallow two-level crawl:
the homepage, then the issue/press-release *listing* pages it links to, and from those the
individual issue and press-release pages. Their text is concatenated for the LLM to score.

Best-effort and failure-tolerant: a member whose site is down or empty just yields no text
(the stage skips them) rather than aborting a 431-member run.
"""

import re
import requests
from urllib.parse import urljoin, urlparse

UA = {"User-Agent": "watchgov"}

_LINK = re.compile(r'href=["\']([^"\']+)["\']', re.I)
# Links whose URL suggests a stated-position page or a statement/press release.
_RELEVANT = re.compile(
    r"issue|priorit|press-release|newsroom|statement|where-i-stand|on-the-issues", re.I
)
# Prefer genuine "positions" pages over individual press releases when ordering.
_POSITIONS = re.compile(r"issue|priorit|where-i-stand|on-the-issues", re.I)
# Section index/listing pages (no per-item slug) that we crawl to discover individual items.
_LISTING_LEAVES = {
    "press-releases", "press", "newsroom", "news", "media",
    "issues", "priorities", "on-the-issues",
}
_STRIP_BLOCKS = re.compile(
    r"(?is)<(script|style|nav|footer|header|form|noscript)[^>]*>.*?</\1>"
)
_TAG = re.compile(r"(?s)<[^>]+>")
_WS = re.compile(r"\s+")


def _get(url: str, timeout: int) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def _to_text(html: str) -> str:
    """Strip a page to readable text (drop scripts/nav/chrome, collapse whitespace)."""
    return _WS.sub(" ", _TAG.sub(" ", _STRIP_BLOCKS.sub(" ", html))).strip()


def _is_listing(url: str) -> bool:
    """A section index (…/press-releases, …/issues) rather than an individual item."""
    leaf = urlparse(url).path.rstrip("/").split("/")[-1].lower()
    return leaf in _LISTING_LEAVES


def _relevant_links(html: str, from_url: str, base: str) -> list[str]:
    out: list[str] = []
    for m in _LINK.finditer(html):
        href = urljoin(from_url, m.group(1))
        if urlparse(href).netloc == base and _RELEVANT.search(href):
            out.append(href.split("#")[0])
    return out


def gather_member_text(
    website: str,
    *,
    max_pages: int = 14,
    max_listings: int = 3,
    per_page_chars: int = 2200,
    total_chars: int = 22000,
    timeout: int = 20,
) -> tuple[str, list[str]]:
    """Return (corpus_text, source_urls) of a member's public statements.

    Crawls homepage → issue/press-release listing pages → individual items, then concatenates
    the extracted text (positions pages first, then press releases) up to `total_chars`.
    Returns ("", []) if the site can't be reached.
    """
    site = website.rstrip("/")
    base = urlparse(site).netloc
    try:
        home = _get(site, timeout)
    except Exception:
        return "", []

    home_links = _relevant_links(home, site, base)
    listings = [l for l in home_links if _is_listing(l) and l != site]
    items = [l for l in home_links if not _is_listing(l)]

    # Second level: pull individual items out of the listing pages (this is what turns AOC's
    # 4 homepage-linked releases into a dozen recent ones).
    seen_items = dict.fromkeys(items)
    for lst in dict.fromkeys(listings[:max_listings]):
        try:
            for href in _relevant_links(_get(lst, timeout), lst, base):
                if not _is_listing(href):
                    seen_items.setdefault(href, None)
        except Exception:
            continue

    # Positions/issue items first, then press releases; shorter URLs (section roots) first.
    candidates = list(seen_items) + listings  # keep issue listing roots as content too
    candidates.sort(key=lambda h: (0 if _POSITIONS.search(h) else 1, len(h)))

    corpus = [_to_text(home)[:per_page_chars]]
    sources = [site]
    for href in candidates:
        if href in sources or len(sources) > max_pages:
            continue
        try:
            text = _to_text(_get(href, timeout))
        except Exception:
            continue
        if len(text) > 200:
            corpus.append(text[:per_page_chars])
            sources.append(href)

    joined = "\n\n---\n".join(f"[{u}]\n{c}" for u, c in zip(sources, corpus))
    return joined[:total_chars], sources
