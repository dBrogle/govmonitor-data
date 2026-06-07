"""Download official congressional portraits for tracked members.

Pulls each candidate's portrait (keyed by bioguide id) from the unitedstates project's
public-domain image set into the frontend's public/members/ so the app serves them
locally — more stable than hotlinking. Re-run after adding candidates.

    python data/scripts/download_member_images.py [--force]

Skips members whose image already exists unless --force is passed.
"""

import argparse
import glob
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PROFILES_GLOB = os.path.join(REPO, "data", "pipeline", "output", "s5_alignment", "*.json")
DEST = os.path.join(REPO, "web_app", "frontend", "public", "members")
# 450x550 is the largest "sized" portrait; "original" and "225x275" also exist.
URL = "https://unitedstates.github.io/images/congress/450x550/{bioguide}.jpg"


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
    ok = skipped = fail = 0
    for bioguide, name in sorted(members.items()):
        out = os.path.join(DEST, f"{bioguide}.jpg")
        if os.path.exists(out) and not args.force:
            skipped += 1
            continue
        try:
            req = urllib.request.Request(URL.format(bioguide=bioguide), headers={"User-Agent": "govstalker"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 1000:
                raise ValueError("suspiciously small image")
            with open(out, "wb") as fh:
                fh.write(data)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  FAIL {bioguide} ({name}): {e}")

    print(f"Done: {ok} downloaded, {skipped} already present, {fail} failed")


if __name__ == "__main__":
    main()
