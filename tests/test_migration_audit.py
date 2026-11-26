import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from migration_audit import audit, classify

BLOB = ("100644", "a" * 40)
MASTER = ("100755", "b" * 40)


@pytest.fixture
def migration():
    before = {
        "logos/teams/A.png": BLOB, "logos/source/teams/A.png": MASTER,
        "logos/source/teams/retired/A 2026-01-01.png": BLOB,
        "logos/teams/glow-dark/A.png": BLOB, "logos/logo jackpot/B.png": BLOB,
        "trophies/A.png": BLOB, "readme.md": BLOB,
    }
    app = {"assets/logos/teams/A.png": BLOB}
    after = {
        "Master/logos/teams/A.png": MASTER, "Archive/logos/teams/A 2026-01-01.png": BLOB,
        "Unused/logos/logo jackpot/B.png": BLOB, "Master/trophies/A.png": BLOB,
        "Transfer/.gitkeep": ("100644", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
        "readme.md": BLOB,
    }
    return before, app, after


def test_accounting_and_app_matching(migration):
    before, app, after = migration
    rows = classify(before, app)
    assert len({row["action"] for row in rows}) == 5
    assert audit(before, app, after, rows) == 6
    with pytest.raises(ValueError, match="exactly once"):
        audit(before, app, after, rows + rows[:1])
    app["assets/logos/teams/A.png"] = MASTER
    with pytest.raises(ValueError, match="App blob differs"):
        classify(before, app)
    del before["logos/teams/A.png"]
    with pytest.raises(ValueError, match="missing production"):
        classify(before, app)


@pytest.mark.parametrize("path", ["readme.md", "Master/trophies/A.png", "Master/logos/teams/A.png"])
def test_unauthorized_removal(migration, path):
    before, app, after = migration
    del after[path]
    with pytest.raises(ValueError, match="Missing or altered"):
        audit(before, app, after, classify(before, app))


def test_mode_and_unexpected_file(migration):
    before, app, after = migration
    after["Master/logos/teams/A.png"] = ("100644", MASTER[1])
    after["Master/logos/extra.png"] = BLOB
    with pytest.raises(ValueError, match="unauthorized paths"):
        audit(before, app, after, classify(before, app))
