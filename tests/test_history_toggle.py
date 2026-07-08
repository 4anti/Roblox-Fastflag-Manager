import json

from src.core.flag_manager import FlagManager
from src.utils.config import Config


def _fm(monkeypatch, tmp_path):
    """A FlagManager with history pointed at a temp file."""
    monkeypatch.setattr(Config, "HISTORY_FILE", tmp_path / "history.json")
    fm = FlagManager()
    fm.user_flags = [{"name": "FFlagExample", "value": "true", "type": "bool"}]
    return fm


def test_history_off_at_zero_skips_save(tmp_path, monkeypatch):
    # Slider at "Off" (0) => no snapshot written.
    fm = _fm(monkeypatch, tmp_path)
    fm.save_history_snapshot("should be skipped", 0)
    assert not (tmp_path / "history.json").exists()


def test_history_negative_skips_save(tmp_path, monkeypatch):
    fm = _fm(monkeypatch, tmp_path)
    fm.save_history_snapshot("should be skipped", -1)
    assert not (tmp_path / "history.json").exists()


def test_history_positive_saves(tmp_path, monkeypatch):
    fm = _fm(monkeypatch, tmp_path)
    fm.save_history_snapshot("kept", 30)
    hf = tmp_path / "history.json"
    assert hf.exists()
    data = json.loads(hf.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["action"] == "kept"


def test_history_off_does_not_append_to_existing(tmp_path, monkeypatch):
    fm = _fm(monkeypatch, tmp_path)
    fm.save_history_snapshot("first", 30)
    fm.save_history_snapshot("second", 0)  # Off => must NOT be added
    data = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert [s["action"] for s in data] == ["first"]
