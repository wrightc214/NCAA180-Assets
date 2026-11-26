import sys
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from promote_assets import promote

def test_promotion(tmp_path):
    root, app = tmp_path / "asset", tmp_path / "app"
    src = root / "logos/CCG/a.png"
    src.parent.mkdir(parents=True)
    app.mkdir()
    im = Image.new("RGBA", (1000, 1000))
    ImageDraw.Draw(im).rectangle((100,100,900,900), fill="red")
    im.save(src)
    original = src.read_bytes()
    assert len(promote(root, app, "logos/CCG")) == 1
    assert src.read_bytes() == original
    assert max(Image.open(app / "assets/logos/CCG/a.png").size) == 512
    assert promote(root, app, "logos/CCG") == []
    im.save(src)
    assert src.read_bytes() == original

@pytest.mark.parametrize("selection", ["logos/source", "logos/logo jackpot", "logos/../app", "logos", "/tmp"])
def test_reject(tmp_path, selection):
    with pytest.raises(ValueError):
        promote(tmp_path / "asset", tmp_path / "app", selection)
