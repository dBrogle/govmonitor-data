import json
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "data"


def cache_path(endpoint: str, params: dict, folder: str) -> Path:
    """Build a human-readable cache file path for a given endpoint + params.

    Each cache usage must specify a folder (e.g. "bills", "fundraising") so
    that distinct data types each get their own directory.
    """
    base = CACHE_DIR / folder
    base.mkdir(parents=True, exist_ok=True)
    safe_params = {k: v for k, v in sorted(params.items())}
    parts = "__".join(f"{k}={v}" for k, v in safe_params.items())
    name = f"{endpoint}__{parts}.json" if parts else f"{endpoint}.json"
    return base / name


def load_cache(path: Path) -> dict | None:
    """Return parsed JSON from a cache file, or None if it doesn't exist."""
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_cache(path: Path, data: dict) -> None:
    """Write raw response data to a cache file as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
