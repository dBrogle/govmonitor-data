"""
Pipeline runner — orchestrates candidate data collection stages.

Run from the data/ directory:
    python pipeline/run.py                    # run all stages
    python pipeline/run.py members            # run just one stage
    python pipeline/run.py members finance    # run specific stages
    python pipeline/run.py --force analysis   # re-run even if output exists

Stages (in order):
    1. members    — Congressional profiles, legislation, voting history
    2. finance    — Campaign finance data from OpenFEC
    3. bills      — Deep-dive bill data for all referenced bills
    4. analysis   — LLM topic scoring (requires OPENROUTER_API_KEY)
    5. alignment  — Per-topic alignment scores from votes + analysis
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from pipeline.stages import (
    s1_members, s2_finance, s3_bills, s4_analysis, s5_alignment,
)

PIPELINE_DIR = Path(__file__).parent

STAGES = {
    "members": ("Congressional profiles & voting history", s1_members),
    "finance": ("Campaign finance data", s2_finance),
    "bills": ("Deep-dive bill data", s3_bills),
    "analysis": ("LLM topic scoring", s4_analysis),
    "alignment": ("Member alignment scores", s5_alignment),
}

STAGE_ORDER = ["members", "finance", "bills", "analysis", "alignment"]


def load_candidates() -> list[dict]:
    path = PIPELINE_DIR / "candidates.json"
    if not path.exists():
        print(f"[error] candidates.json not found at {path}")
        sys.exit(1)
    return json.loads(path.read_text())


def load_config() -> dict:
    path = PIPELINE_DIR / "config.json"
    if not path.exists():
        print(f"[error] config.json not found at {path}")
        sys.exit(1)
    config = json.loads(path.read_text())

    # Inject API keys from environment. api.data.gov keys are universal (one key works for
    # both Congress.gov and OpenFEC), and the rate limit is per key — so we load a pool of
    # numbered keys (CONGRESS_API_KEY_1, _2, ...) and the services round-robin across them.
    api_keys = _load_api_keys()
    config["_congress_api_key"] = api_keys
    config["_fec_api_key"] = api_keys
    config["_openrouter_api_key"] = os.getenv("OPENROUTER_API_KEY")

    # One thread per key for the rate-limited finance stage: throughput is capped per key,
    # so concurrency only helps up to the number of keys.
    config["finance_parallel"] = len(api_keys)
    config["members_parallel"] = max(config.get("members_parallel", 8), len(api_keys))
    print(f"  API keys: {len(api_keys)} → finance_parallel={config['finance_parallel']}")

    return config


def _load_api_keys() -> list[str]:
    """Collect api.data.gov keys: numbered CONGRESS_API_KEY_1.. take precedence, with a
    fallback to the legacy single CONGRESS_API_KEY / OPEN_FEC_API_KEY vars."""
    keys: list[str] = []
    i = 1
    while True:
        k = os.getenv(f"CONGRESS_API_KEY_{i}")
        if not k or k == "put_key_here":
            break
        keys.append(k)
        i += 1
    if not keys:
        for name in ("CONGRESS_API_KEY", "OPEN_FEC_API_KEY"):
            v = os.getenv(name)
            if v and v not in keys:
                keys.append(v)
    return keys or ["DEMO_KEY"]


def show_menu() -> tuple[list[str], bool]:
    """Interactive menu for selecting stages. Returns (stage_names, force)."""
    print(f"\n{'═' * 60}")
    print(f"  govstalker pipeline — select stages")
    print(f"{'═' * 60}")
    for i, name in enumerate(STAGE_ORDER, 1):
        desc, _ = STAGES[name]
        print(f"    {i}. {name:<12} — {desc}")
    print(f"    a. Run all")
    print(f"{'═' * 60}")

    choice = input("\n  Select stages (comma-separated, e.g. 1,4,5 or a): ").strip().lower()
    if choice == "a" or choice == "":
        selected = list(STAGE_ORDER)
    else:
        selected = []
        for part in choice.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(STAGE_ORDER):
                selected.append(STAGE_ORDER[int(part) - 1])
            elif part in STAGE_ORDER:
                selected.append(part)

    if not selected:
        print("  No valid stages selected.")
        sys.exit(0)

    force_input = input("  Force re-run existing outputs? [y/N]: ").strip().lower()
    force = force_input in ("y", "yes")

    return selected, force


def main():
    parser = argparse.ArgumentParser(
        description="Run the govstalker data pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "stages",
        nargs="*",
        default=[],
        help="Stages to run (default: interactive menu). Options: " + ", ".join(STAGE_ORDER),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run stages even if output already exists.",
    )
    args = parser.parse_args()

    # Validate stage names if provided via CLI
    for s in args.stages:
        if s not in STAGE_ORDER:
            parser.error(f"Unknown stage '{s}'. Options: {', '.join(STAGE_ORDER)}")

    candidates = load_candidates()
    config = load_config()

    if args.stages:
        stages_to_run = args.stages
        force = args.force
    else:
        stages_to_run, force = show_menu()

    print(f"\n{'═' * 60}")
    print(f"  govstalker pipeline")
    print(f"  Candidates: {len(candidates)}")
    print(f"  Stages: {', '.join(stages_to_run)}")
    print(f"  Force: {force}")
    print(f"{'═' * 60}")

    for stage_name in STAGE_ORDER:
        if stage_name not in stages_to_run:
            continue

        desc, module = STAGES[stage_name]
        print(f"\n{'─' * 60}")
        print(f"  Stage: {stage_name} — {desc}")
        print(f"{'─' * 60}")

        module.run(candidates, config, force=force)

    print(f"\n{'═' * 60}")
    print(f"  Pipeline complete.")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
