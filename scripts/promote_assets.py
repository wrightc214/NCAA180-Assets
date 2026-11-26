"""Validate, render and stage a complete Transfer batch."""
import argparse
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess

from PIL import Image


def trimmed(im, config):
    """im cropped to its visible pixels plus an even margin; None if nothing to change."""
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return None  # fully opaque: no transparent padding to remove
    bb = alpha.point(lambda v: 255 if v > config["alpha_min"] else 0).getbbox()
    if not bb:
        return None
    w, h = im.size
    cw, ch = bb[2] - bb[0], bb[3] - bb[1]
    pad = round(max(cw, ch) * config["margin"])
    gaps = (bb[0], bb[1], w - bb[2], h - bb[3])
    if max(abs(g - pad) for g in gaps) <= max(2, round(max(cw, ch) * config["tolerance"])):
        return None
    out = Image.new("RGBA", (cw + 2 * pad, ch + 2 * pad), (0, 0, 0, 0))
    out.paste(im.crop(bb), (pad, pad))
    return out


def web_copy(base, config):
    with Image.open(base, formats=["PNG"]) as original:
        im = original.convert("RGBA")
    out = trimmed(im, config) or im
    max_side = config["max_side"]
    if max(out.size) > max_side:
        out.thumbnail((max_side, max_side), Image.LANCZOS)
    return im, out


def png_bytes(im):
    import io
    b = io.BytesIO()
    im.save(b, format="PNG", optimize=True)
    return b.getvalue()


def same_pixels(a, b):
    a, b = a.convert("RGBA"), b.convert("RGBA")
    return a.size == b.size and a.tobytes() == b.tobytes()


@dataclass
class Promotion:
    manifest: dict
    transfer: Path
    master: Path
    app: Path
    archive: Path | None
    identical_master: bool
    rendered: bytes


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *map(str, args)])


def parts(path):
    components = path.split("/")
    if (any(piece in {"", ".", ".."} or piece.casefold() == ".git"
            or piece.endswith((" ", ".")) for piece in components)
            or any(ord(char) < 32 or ord(char) == 127 or char in '\\:*?"<>|' for char in path)):
        raise ValueError(f"Invalid path: {path!r}")
    return tuple(components)


def below(path, prefix):
    prefix_parts = parts(prefix)
    return tuple(component.casefold() for component in parts(path)[:len(prefix_parts)]) == tuple(
        component.casefold() for component in prefix_parts
    )


def safe_path(root, relative, *, directory=False):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Invalid repository root: {root}")
    components = parts(relative)
    path = root
    for index, name in enumerate(components):
        if path.is_dir():
            for child in path.iterdir():
                if child.name != name and child.name.casefold() == name.casefold():
                    raise ValueError(f"Case collision: {path / name} and {child}")
        path /= name
        if path.is_symlink():
            raise ValueError(f"Symlink in path: {path}")
        if path.exists():
            expected = stat.S_ISDIR if directory or index < len(components) - 1 else stat.S_ISREG
            if not expected(path.stat().st_mode):
                raise ValueError(f"Path conflict: {path}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes repository: {path}")
    return path


def transfer_files(asset):
    transfer = safe_path(asset, "Transfer", directory=True)
    if not transfer.exists():
        return []
    names = []
    for folder, directories, files in os.walk(transfer, followlinks=False):
        for name in directories:
            candidate = Path(folder, name).relative_to(asset).as_posix()
            safe_path(asset, candidate, directory=True)
        for name in files:
            candidate = Path(folder, name).relative_to(asset).as_posix()
            safe_path(asset, candidate)
            if name != ".gitkeep":
                names.append(candidate.removeprefix("Transfer/"))
    return sorted(names)


def reserve(path, reserved):
    key = path.as_posix().casefold()
    if key in reserved or any(str(parent).casefold() in reserved for parent in path.parents):
        raise ValueError(f"Batch destination collision: {path}")
    if any(item.startswith(key + "/") for item in reserved):
        raise ValueError(f"Batch path conflict: {path}")
    reserved.add(key)


def archive_path(asset, relative, today, reserved):
    path = PurePosixPath(relative)
    template = f"Archive/{path.with_suffix('')} {today.isoformat()}"
    suffix = ""
    counter = 2
    while True:
        candidate = safe_path(asset, template + suffix + path.suffix)
        if not candidate.exists() and candidate.as_posix().casefold() not in reserved:
            reserve(candidate, reserved)
            return candidate
        suffix = f" ({counter})"
        counter += 1


def dimensions(data):
    if data is None:
        return None
    try:
        with Image.open(BytesIO(data), formats=["PNG"]) as image:
            image.load()
            return list(image.size)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def digest(data):
    return sha256(data).hexdigest() if data is not None else None


def prepare(asset, app, config, today):
    reserved = set()
    batch = []
    for relative in transfer_files(asset):
        if any(below(relative, prefix) for prefix in config["never_promote"]):
            raise ValueError(f"Never-promote path: {relative}")
        ext = PurePosixPath(relative).suffix
        if ext.casefold() == ".png" and ext != ".png":
            raise ValueError(f"Use a lowercase .png extension: {relative}")
        transfer = safe_path(asset, "Transfer/" + relative)
        master = safe_path(asset, "Master/" + relative)
        safe_path(asset, "Archive", directory=True)
        app_relative = config["app_prefix"] + "/" + relative
        destination = safe_path(app, app_relative)
        reserve(master, reserved)
        reserve(destination, reserved)
        source = transfer.read_bytes()
        old = destination.read_bytes() if destination.exists() else None
        trim = (ext == ".png"
                and any(below(relative, folder) for folder in config["trim_folders"])
                and not any(below(relative, folder) for folder in config["skip_folders"]))
        equal = old == source
        rendered = source
        if trim:
            try:
                original, output = web_copy(BytesIO(source), config)
                rendered = source if original is output and max(dimensions(source)) <= config["max_side"] else png_bytes(output)
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                raise ValueError(f"Invalid PNG: {relative}") from error
            equal = False
            if dimensions(old):
                with Image.open(BytesIO(old)) as existing:
                    equal = same_pixels(existing, output)
        action = "UNCHANGED" if equal else "ADD" if old is None else "REPLACE"
        identical = master.exists() and master.read_bytes() == source
        archive = archive_path(asset, relative, today, reserved) if master.exists() and not identical else None
        new = old if equal else rendered
        row = {
            "action": action, "app_path": app_relative, "transfer_path": transfer.relative_to(asset).as_posix(),
            "master_path": master.relative_to(asset).as_posix(), "identical_master": identical,
            "old_sha256": digest(old), "new_sha256": digest(new),
            "old_dimensions": dimensions(old) if ext == ".png" else None,
            "new_dimensions": dimensions(new) if ext == ".png" else None,
            "processing": "trimmed" if trim else "pass-through",
            "archived_master_path": archive.relative_to(asset).as_posix() if archive else None,
        }
        batch.append(Promotion(row, transfer, master, destination, archive, identical, rendered))
    return batch


def authorized_paths(rows):
    asset, app = set(), set()
    for row in rows:
        asset.add(row["transfer_path"])
        if not row["identical_master"]:
            asset.add(row["master_path"])
        if row["archived_master_path"]:
            asset.add(row["archived_master_path"])
        if row["action"] in {"ADD", "REPLACE"}:
            app.add(row["app_path"])
    return asset, app


def assert_changes(repo, paths):
    staged = git(repo, "diff", "--cached", "--no-renames", "--name-only", "-z")
    actual = set(staged.decode().split("\0")) - {""}
    if actual != paths or git(repo, "diff", "--name-only", "-z") or git(
        repo, "ls-files", "--others", "--exclude-standard", "-z"
    ):
        raise ValueError(f"Unexpected prepared changes in {repo}: "
                         f"missing={sorted(paths - actual)} extra={sorted(actual - paths)}")


def stage(asset, app, batch):
    for entry in batch:
        if entry.archive:
            entry.archive.parent.mkdir(parents=True, exist_ok=True)
            git(asset, "mv", "--", entry.master.relative_to(asset), entry.archive.relative_to(asset))
        if entry.identical_master:
            git(asset, "rm", "--", entry.transfer.relative_to(asset))
        else:
            entry.master.parent.mkdir(parents=True, exist_ok=True)
            git(asset, "mv", "--", entry.transfer.relative_to(asset), entry.master.relative_to(asset))
        if entry.manifest["action"] != "UNCHANGED":
            entry.app.parent.mkdir(parents=True, exist_ok=True)
            entry.app.write_bytes(entry.rendered)
            git(app, "add", "--", entry.app.relative_to(app))
    asset_paths, app_paths = authorized_paths([entry.manifest for entry in batch])
    assert_changes(asset, asset_paths)
    assert_changes(app, app_paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path)
    parser.add_argument("app", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    asset, app = args.asset.absolute(), args.app.absolute()
    config = json.loads((asset / "config/promotion.json").read_text(encoding="utf-8"))
    for repo in (asset, app):
        assert_changes(repo, set())
    batch = prepare(asset, app, config, date.today())
    manifest = {
        "asset_base": git(asset, "rev-parse", "HEAD").decode().strip(),
        "app_base": git(app, "rev-parse", "HEAD").decode().strip(),
        "files": [entry.manifest for entry in batch],
    }
    if batch:
        import publish_promotion
        publish_promotion.check_duplicates(os.environ["GITHUB_REPOSITORY"], config["app_repo"], manifest["files"])
        stage(asset, app, batch)
    else:
        print("Nothing to promote")
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
