"""Download official congressional portraits for tracked members.

Pulls each member's portrait (keyed by bioguide id) into the frontend's public/members/
so the app serves them locally — more stable than hotlinking. Re-run after adding members.

Primary source is the unitedstates project's public-domain image set. Recently-seated
members (e.g. special-election winners) are often missing there, so we fall back to
Congress.gov's own member depiction (needs CONGRESS_API_KEY in data/.env).

    python data/scripts/download_member_images.py [--force]

Skips members whose image already exists unless --force is passed.
"""

import argparse
import glob
import json
import os
import re
import urllib.request

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PROFILES_GLOB = os.path.join(REPO, "data", "pipeline", "output", "s5_alignment", "*.json")
DEST = os.path.join(REPO, "web_app", "frontend", "public", "members")
# 450x550 is the largest "sized" portrait; "original" and "225x275" also exist.
CDN_URL = "https://unitedstates.github.io/images/congress/450x550/{bioguide}.jpg"

load_dotenv(os.path.join(REPO, "data", ".env"))
CONGRESS_KEY = os.getenv("CONGRESS_API_KEY_1") or os.getenv("CONGRESS_API_KEY")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "govstalker"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if len(data) < 1000:
        raise ValueError("suspiciously small image")
    return data


def congress_depiction_url(bioguide: str) -> str | None:
    """Congress.gov's own portrait URL for a member, via the member detail API."""
    if not CONGRESS_KEY:
        return None
    url = f"https://api.congress.gov/v3/member/{bioguide}?format=json&api_key={CONGRESS_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "govstalker"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    img = ((data.get("member") or {}).get("depiction") or {}).get("imageUrl")
    # The API hands back a thumbnail (e.g. ..._200.jpg); dropping the size suffix
    # gives the full-resolution original (~1000px).
    return re.sub(r"_\d+(\.jpg)$", r"\1", img) if img else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-download existing images")
    args = parser.parse_args()

    os.makedirs(DEST, exist_ok=True)
    members = {}
    for f in glob.glob(PROFILES_GLOB):
        p = json.load(open(f)).get("profile") or {}
        if p.get("bioguide_id"):
            members[p["bioguide_id"]] = p.get("name", p["bioguide_id"])

    print(f"{len(members)} members; downloading to {DEST}/")
    ok = skipped = fallback = fail = 0
    for bioguide, name in sorted(members.items()):
        out = os.path.join(DEST, f"{bioguide}.jpg")
        if os.path.exists(out) and not args.force:
            skipped += 1
            continue

        data = None
        try:
            data = fetch_bytes(CDN_URL.format(bioguide=bioguide))
        except Exception:
            # Fall back to Congress.gov's depiction for members the CDN lacks.
            try:
                img = congress_depiction_url(bioguide)
                if img:
                    data = fetch_bytes(img)
                    fallback += 1
                    print(f"  [congress.gov] {bioguide} ({name})")
            except Exception as e:
                print(f"  FAIL {bioguide} ({name}): {e}")

        if data:
            with open(out, "wb") as fh:
                fh.write(data)
            ok += 1
        elif not os.path.exists(out):
            fail += 1
            print(f"  MISS {bioguide} ({name}): no image from either source")

    print(f"Done: {ok} downloaded ({fallback} via congress.gov), {skipped} present, {fail} missing")


if __name__ == "__main__":
    main()
