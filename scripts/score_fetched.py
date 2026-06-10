"""Flash-score all fetched bills that aren't scored yet (sponsored/cosponsored included).

Runs s4 with analyze_voted_only=False so sponsored/cosponsored bills are scored too, on the
Phase 2 model. No force: already-scored bills are skipped, so this only scores the new ones.
Does NOT run s5 — alignment is still vote-only until sponsorship weighting is wired in, so the
candidate alignment numbers won't change; this just makes the bills' topic scores available.

Safe to run while the download is still fetching: s4 skips any bill file that's mid-write.

Run from data/:  python scripts/score_fetched.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from pipeline.stages import s4_analysis

DATA_ROOT = Path(__file__).parent.parent
SCORING_MODEL = "google/gemini-2.5-flash"


def _api_keys():
    keys = []
    i = 1
    while (k := os.getenv(f"CONGRESS_API_KEY_{i}")) and k != "put_key_here":
        keys.append(k); i += 1
    if not keys and (s := os.getenv("CONGRESS_API_KEY")):
        keys.append(s)
    return keys


def main():
    load_dotenv(DATA_ROOT / ".env")
    candidates = json.loads((DATA_ROOT / "pipeline" / "candidates.json").read_text())
    config = {
        "congress": 119,
        "_openrouter_api_key": os.getenv("OPENROUTER_API_KEY"),
        "_congress_api_key": _api_keys(),
        "llm_model": SCORING_MODEL,
        "parallel_llm_calls": 20,
        "analyze_voted_only": False,   # include sponsored/cosponsored bills
    }
    print(f"=== s4: flash-scoring fetched bills ({SCORING_MODEL}) ===")
    s4_analysis.run(candidates, config, force=False)
    print("\n=== done ===")


if __name__ == "__main__":
    main()
