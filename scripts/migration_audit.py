"""Generate the migration manifest from Git trees and audit a committed migration."""
import argparse
import csv
from pathlib import Path
import subprocess

ART = ("app/", "bots/", "fonts/", "logos/", "trophies/")
FIELDS = ("original_path", "new_path", "original_blob_id", "original_mode",
          "action", "app_path", "app_blob_id")
REPLACED = {".github/workflows/logo-trim.yml", "scripts/logo_trim.py",
            "tests/test_logo_trim.py"}
ADDED = {"Transfer/.gitkeep", "migration-manifest.csv", "PR_README.md",
         ".github/workflows/promote-assets.yml", "config/promotion.json",
         "scripts/migration_audit.py", "scripts/promote_assets.py",
         "scripts/publish_promotion.py", "tests/test_migration_audit.py",
         "tests/test_promote_assets.py"}


def tree(repo, ref):
    data = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-rz", ref])
    entries = {}
    for entry in data.decode().split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            mode, kind, blob = meta.split()
            entries[path] = (mode, blob)
    return entries


def classify(asset, app):
    sources = {}
    for path in asset:
        if path.startswith("logos/source/"):
            relative = path.removeprefix("logos/source/")
            if "/retired/" not in relative:
                web = "logos/" + relative
                if web not in asset:
                    raise ValueError(f"Master missing production file: {path}")
                sources[web] = path
    rows = []
    destinations = set()
    for path, (mode, blob) in sorted(asset.items()):
        if not path.startswith(ART):
            continue
        app_path = app_blob = ""
        dest, action = "Master/" + path, "MOVE_MASTER"
        if path.startswith("logos/teams/glow-dark/"):
            dest, action = "", "REMOVE_GENERATED_GLOW"
        elif path.startswith("logos/logo jackpot/"):
            dest, action = "Unused/" + path, "MOVE_UNUSED"
        elif path.startswith("logos/source/"):
            relative = path.removeprefix("logos/source/")
            if "/retired/" in relative:
                dest = "Archive/logos/" + relative.replace("/retired/", "/", 1)
                action = "MOVE_ARCHIVE"
            else:
                dest = "Master/logos/" + relative
        elif path in sources:
            app_path = "assets/" + path
            app_blob = app.get(app_path, ("", ""))[1]
            if app_blob != blob:
                raise ValueError(f"Cannot discard web copy; App blob differs: {path}")
            dest, action = "", "REMOVE_SUPERSEDED_WEB_COPY"
        if dest:
            if dest.casefold() in destinations:
                raise ValueError(f"Migration destination collision: {dest}")
            destinations.add(dest.casefold())
        rows.append(dict(zip(FIELDS, (path, dest, blob, mode, action, app_path, app_blob))))
    return rows


def audit(before, app, after, rows):
    expected = classify(before, app)
    if rows != expected:
        raise ValueError("Manifest does not account for each original art path exactly once")
    required = {path: entry for path, entry in before.items()
                if not path.startswith(ART) and path not in REPLACED}
    required.update({row["new_path"]: (row["original_mode"], row["original_blob_id"])
                     for row in expected if row["new_path"]})
    differences = sorted(path for path, entry in required.items() if after.get(path) != entry)
    unexpected = sorted(after.keys() - required.keys() - ADDED)
    if differences or unexpected:
        raise ValueError(f"Missing or altered files: {differences}; unauthorized paths: {unexpected}")
    placeholder = ("100644", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
    if after.get("Transfer/.gitkeep") != placeholder:
        raise ValueError("Transfer must initially contain only an empty .gitkeep")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_base")
    parser.add_argument("app_repo", type=Path)
    parser.add_argument("app_base")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    with Path("migration-manifest.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    count = audit(tree(".", args.asset_base), tree(args.app_repo, args.app_base),
                  tree(".", args.head), rows)
    print(f"Verified {count} migration records")


if __name__ == "__main__":
    main()
