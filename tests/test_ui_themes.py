"""New UI themes must land next to the existing ones without moving the
Sortable script tag (S4 hashes that region) or dropping old option values.
"""
import os

from src.gui.api import Api
from src.utils.config import Config


UI_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "gui", "ui")
INDEX = os.path.join(UI_DIR, "index.html")
FONTS = os.path.join(UI_DIR, "fonts")

OLD_THEMES = ("legacy", "ignite", "matrix", "cherry", "premium")
NEW_THEMES = ("studio", "nocturne", "mocha", "vellum", "aurora", "neko")
THEME_CLASSES = (
    "theme-legacy",
    "theme-ignite",
    "theme-matrix",
    "theme-cherry",
    "theme-studio",
    "theme-nocturne",
    "theme-mocha",
    "theme-vellum",
    "theme-aurora",
    "theme-neko",
)
REQUIRED_FONTS = (
    "inter-400.woff2",
    "syne-700.woff2",
    "instrument-serif-400.woff2",
    "plus-jakarta-sans-400.woff2",
    "newsreader-400.woff2",
    "source-sans-3-400.woff2",
    "ibm-plex-mono-400.woff2",
    "space-grotesk.woff2",
    "outfit.woff2",
)


def _index_text():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _index_bytes():
    with open(INDEX, "rb") as f:
        return f.read()


def test_sortable_script_tag_untouched():
    data = _index_bytes()
    needle = b'<script src="Sortable.min.js"></script>'
    idx = data.find(needle)
    assert idx >= 0
    # S4 hashes 32 bytes before this tag through 224 bytes after it.
    region = data[max(0, idx - 32) : idx + 224]
    assert needle in region
    assert b"intersection-polyfill" not in region


def test_old_theme_classes_still_present():
    html = _index_text()
    for name in ("theme-legacy", "theme-ignite", "theme-matrix", "theme-cherry"):
        assert f".{name}" in html
    assert 'value="premium"' in html
    for name in OLD_THEMES:
        if name == "premium":
            continue
        assert f'value="{name}"' in html


def test_new_theme_classes_and_options_present():
    html = _index_text()
    for name in NEW_THEMES:
        assert f".theme-{name}" in html
        assert f'value="{name}"' in html
        assert f'"theme-{name}"' in html  # setUITheme classList.remove


def test_aurora_neko_keep_light_toggle_and_canvas():
    html = _index_text()
    start = html.find("const forceDark")
    assert start > 0
    chunk = html[start : start + 350]
    assert "aurora" not in chunk
    assert "neko" not in chunk
    assert 'id="aurora-canvas"' in html
    assert 'id="neko-canvas"' in html
    assert "body.theme-aurora.light-theme" in html
    assert "body.theme-neko.light-theme" in html
    assert "FFM Space Grotesk" in html
    assert "FFM Outfit" in html
    assert "conic-gradient" not in html
    assert 'value="neko"' in html


def test_set_ui_theme_clears_every_skin_class():
    html = _index_text()
    start = html.find("function setUITheme")
    assert start > 0
    chunk = html[start : start + 1800]
    for cls in THEME_CLASSES:
        assert f'"{cls}"' in chunk


def test_bundled_theme_fonts_exist():
    for name in REQUIRED_FONTS:
        path = os.path.join(FONTS, name)
        assert os.path.isfile(path), name
        assert os.path.getsize(path) > 1000, name


def test_set_ui_theme_persists_new_ids(monkeypatch):
    api = Api.__new__(Api)
    api.settings = {}
    monkeypatch.setattr(Config, "save_settings", lambda *_a, **_k: True)
    for name in NEW_THEMES:
        api.set_ui_theme(name)
        assert api.settings["ui_theme"] == name


def test_checker_compare_label_is_not_hash_compare():
    html = _index_text()
    assert "Comparing version hashes" not in html
    assert "Comparing Roblox vs offset dump" in html
    assert "CDN latest:" in html
    assert "Offset dump:" in html
    assert 'key: "compare"' in html
    assert "rvcGuidDetail" in html
