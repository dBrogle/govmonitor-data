# Stated positions & truth score — v1 notes

The `stances` stage (s6) scrapes each member's official house.gov site, LLM-scores their
**stated** position per topic (with a verbatim quote), and s5 pairs that against their
**voted** alignment to produce a per-member **truth score** (agreement between words and votes
on high-confidence topics; null when too few topics are comparable).

## Known limitations (v1 — shipped knowingly)

1. **Sparse coverage on the current 5 topics.** The v1 topics are fiscal (military spending,
   taxation, government spending, trade, foreign aid), but members mostly publicize border,
   health, veterans, and local wins. Many members return a null or 1–2 topic truth score.
   AOC returned **0 topics even at 13 crawled pages** — her messaging (Green New Deal, housing,
   healthcare) doesn't map onto these fiscal axes.
2. **Single-statement volatility.** A stated stance is collapsed to one −1..+1 score from a blob
   of scraped pages, and one press release can dominate — even flip — a topic. Chip Roy's truth
   score moved **66 → 39** between a shallow and a deeper crawl because the deeper crawl surfaced
   *"Rep. Roy Votes Against Bloated $1.15T NDAA"* (stated military −1.00) alongside *"supports
   strengthening our military"* (+1.00). Both are real; the single score can't hold both.
3. **Shallow, heterogeneous scrape.** We crawl homepage → issue/press-release listing → items,
   capped (~14 pages, ~22k chars). Coverage depends on what a given site links and how it's
   structured; JS-gated "issues" pages are missed.
4. **Official-site constraint.** house.gov sites are taxpayer-funded and legally limited in
   campaign-style position statements, so the richest "say" signal is press releases, not a
   clean issues page. Campaign sites would be richer but aren't uniformly available/parseable.

## How to improve (ordered by leverage)

1. **Corroboration gate.** Only assign a topic a stated score when ≥2 statements agree; leave
   one-off mentions null. Directly kills the single-release volatility (#2). Cheap.
2. **Weigh-all-statements prompt + mixed handling.** Prompt the LLM to consider *all* gathered
   statements on a topic and return near-neutral / uncertain when they genuinely conflict,
   rather than latching onto one salient quote.
3. **Per-document scoring + aggregate.** Score each release/page separately and aggregate per
   topic (median/weighted mean). Most stable, but ~5–10× the LLM calls and cost.
4. **Topic set matched to member messaging.** The fiscal 5 are a weak fit for what members say.
   A broader or dynamic topic set (immigration/border, healthcare, veterans…) would lift
   coverage — but must stay aligned with the vote-side topics to remain comparable.
5. **Richer "say" sources.** Floor statements (Congressional Record), vote explanations, and
   official newsletters carry more explicit, per-vote positions than press releases.
6. **Deeper / smarter crawl.** Paginate press-release archives; detect and follow issue
   sub-pages; handle JS-rendered issue pages where present.
7. **Freshness.** Positions change — re-run `stances` periodically (`--force`) and surface the
   `fetched_at` stamp in the UI so users know how current the "says" side is.

See also the design record in the project memory (`truth-score-feature`).
