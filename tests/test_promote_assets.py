from datetime import date
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import sys

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import promote_assets as P
import publish_promotion as publication

CONFIG = json.loads((ROOT / "config/promotion.json").read_text())
TODAY = date(2026, 11, 26)


@pytest.fixture
def repos(tmp_path):
    repos = tmp_path / "asset", tmp_path / "app"
    for repo in repos:
        repo.mkdir()
        P.git(repo, "init", "-q")
    return repos


@pytest.fixture
def png():
    image = Image.new("RGBA", (1000, 1000))
    ImageDraw.Draw(image).rectangle((100, 200, 899, 699), fill="red")
    return P.png_bytes(image)


def write(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def commit(*repos):
    for root in repos:
        P.git(root, "add", "-A")
        P.git(root, "-c", "user.name=reviewer", "-c", "user.email=reviewer@example.invalid",
              "commit", "-qm", "Baseline", "--allow-empty")


def test_classify_stage_archive_and_repeat(repos, png):
    asset, app = repos
    for name in ("A", "B", "C"):
        write(asset, f"Transfer/logos/CCG/{name}.png", png)
    write(asset, "Master/logos/CCG/B.png", b"old master")
    write(asset, "Master/logos/CCG/C.png", png)
    write(asset, "Archive/logos/CCG/B 2026-11-26.png", b"old archive")
    write(app, "assets/logos/CCG/B.png", b"invalid old web copy")
    old = write(app, "assets/logos/CCG/C.png", b"")
    P.web_copy(BytesIO(png), CONFIG)[1].save(old, compress_level=0)
    old_bytes = old.read_bytes()
    commit(*repos)
    batch = P.prepare(asset, app, CONFIG, TODAY)
    assert [item.manifest["action"] for item in batch] == ["ADD", "REPLACE", "UNCHANGED"]
    assert batch[1].manifest["archived_master_path"] == "Archive/logos/CCG/B 2026-11-26 (2).png"
    assert batch[2].identical_master
    P.stage(asset, app, batch)
    assert batch[1].archive.read_bytes() == b"old master"
    assert batch[2].master.read_bytes() == png and old.read_bytes() == old_bytes
    assert P.prepare(asset, app, CONFIG, TODAY) == []
    write(app, "assets/extra.txt", b"unexpected")
    P.git(app, "add", "-A")
    with pytest.raises(ValueError, match="Unexpected"):
        P.assert_changes(app, P.authorized_paths([item.manifest for item in batch])[1])


@pytest.mark.parametrize("path", ["trophies/A.png", "app/icon.png", "bots/A.png", "fonts/a.woff",
                                  "logos/Guillotine/A.png", "logos/Dwight Fight/A.png"])
def test_pass_through(repos, png, path):
    asset, app = repos
    write(asset, f"Transfer/{path}", png)
    [item] = P.prepare(asset, app, CONFIG, TODAY)
    assert item.rendered == png and item.manifest["processing"] == "pass-through"


def test_render_parity(repos, png):
    asset, app = repos
    source = write(asset, "Transfer/logos/CCG/A.png", png)
    [item] = P.prepare(asset, app, CONFIG, TODAY)
    with Image.open(source) as original:
        expected = Image.new("RGBA", (864, 564))
        expected.paste(original.crop((100, 200, 900, 700)), (32, 32))
    expected.thumbnail((512, 512), Image.LANCZOS)
    assert item.rendered == P.png_bytes(expected)
    app_script = ROOT.parent / "app/scripts/logo_trim.py"
    if app_script.is_file():
        spec = importlib.util.spec_from_file_location("app_logo_trim", app_script)
        app_trim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app_trim)
        assert item.rendered == app_trim.png_bytes(app_trim.web_copy(source)[1])


@pytest.mark.parametrize("path", ["logos/source/A.png", "logos/logo jackpot/A.png",
                                  "logos/teams/glow-dark/A.png", "logos/CCG/A.PnG",
                                  "logos/CCG/Bad.png"])
def test_batch_atomicity(repos, png, path):
    asset, app = repos
    good = write(asset, "Transfer/logos/CCG/0.png", png)
    write(asset, f"Transfer/{path}", b"invalid PNG")
    commit(*repos)
    with pytest.raises(ValueError):
        P.prepare(asset, app, CONFIG, TODAY)
    assert good.read_bytes() == png
    for repo in repos:
        P.assert_changes(repo, set())


@pytest.mark.parametrize("path", ["logos/../app", "/tmp", "logos//teams", "logos/./A.png"])
def test_containment(path):
    with pytest.raises(ValueError):
        P.parts(path)


@pytest.mark.parametrize("repo,path,folder", [
    (0, "Transfer/logos/CCG/A.png", False), (0, "Transfer/logos", True),
    (0, "Master/logos/CCG/A.png", False), (0, "Master/logos", True),
    (0, "Archive", True), (1, "assets/logos/CCG/A.png", False), (1, "assets", True),
])
def test_symlinks(repos, tmp_path, png, repo, path, folder):
    asset, app = repos
    outside = tmp_path / "outside"
    outside.mkdir() if folder else outside.touch()
    if not path.startswith("Transfer/"):
        write(asset, "Transfer/logos/CCG/A.png", png)
    link = repos[repo] / path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        P.prepare(asset, app, CONFIG, TODAY)


def test_case_collisions(repos, png):
    asset, app = repos
    write(asset, "Transfer/logos/CCG/A.png", png)
    write(app, "assets/logos/CCG/a.png", png)
    with pytest.raises(ValueError, match="Case collision"):
        P.prepare(asset, app, CONFIG, TODAY)
    write(asset, "Transfer/logos/CCG/a.png", png)
    with pytest.raises(ValueError, match="Case collision"):
        P.prepare(asset, app, CONFIG, TODAY)


@pytest.mark.parametrize("duplicate,file", [("asset", "Transfer/A.png"), ("app", "assets/A.png")])
def test_duplicate_uses_open_prs(monkeypatch, duplicate, file):
    def pages(path, token, **query):
        if path.endswith("/files"):
            return [{"filename": "renamed", "previous_filename": file}] if duplicate in path else []
        assert query == {"state": "open"}
        return [{"number": 7, "html_url": "https://example.invalid/pull/7"}]

    monkeypatch.setattr(publication, "pages", pages)
    monkeypatch.setattr(publication, "api", lambda *args: {"changed_files": 1})
    row = {"transfer_path": "Transfer/A.png", "master_path": "Master/A.png",
           "archived_master_path": None, "app_path": "assets/A.png"}
    with pytest.raises(ValueError, match="https://example.invalid/pull/7"):
        publication.check_duplicates("owner/asset", "owner/app", [row])


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_coordinated_recovery(monkeypatch, tmp_path, cleanup_failure):
    calls = []
    targets = [publication.Target("owner/asset", tmp_path, "promotion/1-1", "token"),
               publication.Target("owner/app", tmp_path, "assets/promotion-1-1", "token")]

    def push(target):
        target.sha = "commit"
        target.push_attempted = True

    def pages(path, *args, **kwargs):
        return [{"number": 13, "head": {"sha": "commit"}}] if "owner/asset" in path else []

    def api(path, token=None, method="GET", payload=None):
        calls.append((path, method))
        if method == "POST" and "owner/app" in path:
            raise RuntimeError("PR creation rejected")
        if method == "PATCH" and cleanup_failure:
            raise RuntimeError("close rejected")
        return {"object": {"sha": "commit"}, "number": 13, "html_url": "https://example.invalid/pull/13"}

    monkeypatch.setattr(publication.Target, "push", push)
    monkeypatch.setattr(publication, "api", api)
    monkeypatch.setattr(publication, "pages", pages)
    pattern = r"owner/asset branch promotion/1-1, PR numbers \[13\]: close PR #13" if cleanup_failure else "cleaned up"
    with pytest.raises(RuntimeError, match=pattern):
        publication.publish(targets, "manifest")
    assert ("/repos/owner/asset/pulls/13", "PATCH") in calls
    assert sum(method == "DELETE" for _, method in calls) == 2
