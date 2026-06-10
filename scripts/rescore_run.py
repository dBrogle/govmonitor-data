"""Re-score bills with the Phase 2 pipeline and regenerate alignment (test run).

Runs s4 (LLM topic scoring) then s5 (alignment aggregation) with force=True, using the
Phase 2 scoring model. Intended for an A/B test of the scoring changes: snapshot the current
s5 output and move the old s4 analyses aside first (the caller does this), then run this and
diff. Logs progress; safe to run while the bill download is going (it only hits OpenRouter and
reads stable, already-cached bill text).

Run from data/:  python scripts/rescore_run.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from pipeline.stages import s4_analysis, s5_alignment

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
        "analyze_voted_only": True,   # test run: only the voted bills that feed today's alignment
    }
    print(f"=== s4: LLM topic scoring ({SCORING_MODEL}) ===")
    s4_analysis.run(candidates, config, force=True)
    print("\n=== s5: alignment aggregation ===")
    s5_alignment.run(candidates, config, force=True)
    print("\n=== done ===")


if __name__ == "__main__":
    main()
