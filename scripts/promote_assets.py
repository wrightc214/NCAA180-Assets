"""Render explicitly selected PNGs into an App checkout without changing Asset files."""
import argparse
import hashlib
from pathlib import Path
from logo_trim import web_copy, png_bytes

def promote(root, app, selection):
    root, app = Path(root).resolve(), Path(app).resolve()
    if root == app or root in app.parents or app in root.parents:
        raise ValueError("Separate checkouts required")
    selected = Path(selection)
    if selected.is_absolute() or ".." in selected.parts or len(selected.parts) < 2 or selected.parts[0] != "logos":
        raise ValueError("Select a logo folder or PNG")
    if any(p.lower() in {"source", "retired", "logo jackpot", "glow-dark", "throwbacks"} for p in selected.parts):
        raise ValueError("Excluded folder")
    source = root / selected
    if not source.exists() or source.resolve() != source:
        raise ValueError("Missing or linked source")
    files = sorted(source.glob("*.png")) if source.is_dir() else [source]
    if not files:
        raise ValueError("No PNGs")
    changes = []
    for file in files:
        if file.suffix != ".png" or not file.is_file() or file.resolve() != file:
            raise ValueError("Expected regular PNG")
        rel = file.relative_to(root)
        master = root / "logos/source" / Path(*rel.parts[1:])
        # Prefer the selected file if it differs from the archived master's web copy.
        from logo_trim import same_pixels
        from PIL import Image
        base = file
        if master.is_file() and master.resolve() == master:
            with Image.open(file) as current:
                if same_pixels(web_copy(master)[1], current):
                    base = master
        data = png_bytes(web_copy(base)[1])
        dest = app / "assets" / rel
        if dest.resolve() != dest:
            raise ValueError("Linked destination")
        old = dest.read_bytes() if dest.exists() else None
        if old == data:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        changes.append(f"{'ADD' if old is None else 'REPLACE'} {dest.relative_to(app)} | source {base.relative_to(root)} | SHA256 {hashlib.sha256(data).hexdigest()}")
    return changes

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--app", required=True)
    p.add_argument("--selection", required=True)
    p.add_argument("--manifest", required=True)
    a = p.parse_args()
    changes = promote(a.root, a.app, a.selection)
    Path(a.manifest).write_text("\n".join(changes) + "\n", encoding="utf-8")
